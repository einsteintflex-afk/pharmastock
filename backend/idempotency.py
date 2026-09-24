# ============================================================
# IDEMPOTENCY KEYS FOR CRITICAL OPERATIONS
# ============================================================
# A client that retries a sale, a delivery receipt, a transfer dispatch /
# receipt, an adjustment or a stock-count posting (double click, flaky mobile
# network) sends the same `Idempotency-Key` header. The first request does
# the work; a repeat gets the stored response and changes nothing.
#
#     replay = idempotency.begin(conn, user, request, body)
#     if replay: return replay
#     ... do the work ...
#     idempotency.finish(conn, request, result)   # same transaction, before commit
#
# The key row is inserted in the operation's own transaction: a concurrent
# duplicate waits on the unique index until the first commits (then replays)
# or rolls back (then runs). Requests without the header behave as before.

import hashlib
import json

import psycopg
from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from .security import CurrentUser

HEADER = "idempotency-key"


def _request_hash(request: Request, body) -> str:
    payload = json.dumps(jsonable_encoder(body), sort_keys=True, default=str)
    return hashlib.sha256(f"{request.method} {request.url.path}\n{payload}".encode()).hexdigest()


def begin(conn: psycopg.Connection, user: CurrentUser, request: Request, body=None) -> JSONResponse | None:
    key = (request.headers.get(HEADER) or "").strip()
    if not key:
        return None
    if len(key) > 100:
        raise HTTPException(status_code=400, detail="Idempotency-Key is too long (max 100 characters)")
    digest = _request_hash(request, body)
    row = conn.execute(
        """
        INSERT INTO idempotency_keys (user_id, key, endpoint, request_hash)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (organization_id, user_id, key) DO NOTHING
        RETURNING id
        """,
        (user.id, key, f"{request.method} {request.url.path}"[:200], digest),
    ).fetchone()
    if row is not None:
        request.state.idempotency_id = row["id"]
        return None
    stored = conn.execute(
        "SELECT request_hash, response, status_code FROM idempotency_keys WHERE user_id = %s AND key = %s",
        (user.id, key),
    ).fetchone()
    if stored["request_hash"] != digest:
        raise HTTPException(status_code=422, detail="This Idempotency-Key was already used for a different request")
    if stored["response"] is None:
        raise HTTPException(status_code=409, detail="The original request is still being processed")
    return JSONResponse(status_code=stored["status_code"] or 200, content=stored["response"],
                        headers={"Idempotent-Replay": "true"})


def finish(conn: psycopg.Connection, request: Request, result, status_code: int = 200) -> None:
    key_id = getattr(request.state, "idempotency_id", None)
    if key_id is None:
        return
    conn.execute(
        "UPDATE idempotency_keys SET response = %s, status_code = %s WHERE id = %s",
        (json.dumps(jsonable_encoder(result), default=str), status_code, key_id),
    )
