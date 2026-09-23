# ============================================================
# AI INVENTORY ASSISTANT
# ============================================================

from typing import Literal

import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import audit
from ..config import settings
from ..database import get_db
from ..security import CurrentUser, require
from ..services import assistant

router = APIRouter(tags=["Assistant"])


class HistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class Question(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    history: list[HistoryItem] = Field(default_factory=list, max_length=12)


@router.get("/assistant/status")
def assistant_status(user: CurrentUser = Depends(require("assistant.use"))):
    return {
        "engine": "claude" if settings.anthropic_api_key else "built-in",
        "model": settings.anthropic_model if settings.anthropic_api_key else None,
        "tools": list(assistant.TOOLS),
    }


@router.post("/assistant/ask")
def ask(body: Question, user: CurrentUser = Depends(require("assistant.use")),
        conn: psycopg.Connection = Depends(get_db)):
    result = assistant.ask(conn, body.question.strip(), [h.model_dump() for h in body.history])
    audit.record(conn, user, "ASSISTANT_QUERY", "assistant", None, None,
                 {"question": body.question[:500], "engine": result.get("engine"),
                  "tools_used": result.get("tools_used")})
    conn.commit()
    return result
