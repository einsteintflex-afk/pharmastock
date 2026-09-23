# ============================================================
# PAGINATION
# ============================================================
# List endpoints accept optional ?limit=&offset=. Without limit they return
# every row, as the original API did. The total number of matching rows is
# always sent in the X-Total-Count header, so clients can page.

from dataclasses import dataclass

from fastapi import Query, Response


@dataclass
class Page:
    limit: int | None
    offset: int


def page_params(limit: int | None = Query(default=None, gt=0, le=5000),
                offset: int = Query(default=0, ge=0, le=10_000_000)) -> Page:
    return Page(limit, offset)


def paginate(rows: list, page: Page, response: Response) -> list:
    """Slice an already-filtered list and report the total."""
    response.headers["X-Total-Count"] = str(len(rows))
    if page.limit is None:
        return rows[page.offset:]
    return rows[page.offset:page.offset + page.limit]


def set_total(response: Response, rows: list[dict], key: str = "total_count") -> list[dict]:
    """For SQL-paged queries selecting COUNT(*) OVER () AS total_count."""
    response.headers["X-Total-Count"] = str(rows[0][key] if rows else 0)
    for row in rows:
        row.pop(key, None)
    return rows
