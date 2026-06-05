"""Public plant catalog endpoint."""

from typing import Annotated

from fastapi import APIRouter, Request
from fastapi.params import Query

from gardenops.db import DB

router = APIRouter()


@router.get("/external-plants")
def search_external(
    q: Annotated[str, Query(min_length=2)] = "",
    *,
    db: DB,
    request: Request,
) -> list[dict]:
    """Search public plant catalog data.

    Garden-owned plant rows are intentionally excluded until the app has a
    separate public species catalog.
    """
    _ = (db, request, q)
    return []
