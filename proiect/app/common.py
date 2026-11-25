"""Helpers shared by controllers."""
from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException, Query, Request, status

BASE = "/api/manager"


def now() -> datetime:
    return datetime.now(timezone.utc)


def oid_or_404(value: str, what: str = "Resource") -> ObjectId:
    """Path ids that are not valid ObjectIds cannot match any resource -> 404."""
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{what} not found")


def oid_or_422(value: str) -> ObjectId:
    """For PUT (create-or-replace) the client chooses the id, so a bad id is a bad value."""
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"'{value}' is not a valid ObjectId (24 hex characters)")


def not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{what} not found")


def conflict(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail=detail)


def unprocessable(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


async def require_json(request: Request) -> None:
    """415 Unsupported Media Type when the body is not JSON."""
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            detail="Request body must be application/json")


class PageParams:
    """?page=...&items_per_page=... (pages are 1-based)."""

    def __init__(self,
                 page: int = Query(1, ge=1, description="1-based page index"),
                 items_per_page: int = Query(10, ge=1, le=100, description="Items on a page")):
        self.page = page
        self.items_per_page = items_per_page

    @property
    def skip(self) -> int:
        return (self.page - 1) * self.items_per_page


def flag(value: str | None) -> bool:
    """Interpret presence-style query flags: `?verbose`, `?verbose=true`, `?verbose=1`."""
    return value is not None and value.strip().lower() not in ("0", "false", "no", "off")
