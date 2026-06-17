from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field

from gardenops.db import DB, current_timestamp_ms
from gardenops.models import StrictBaseModel
from gardenops.router_helpers import (
    active_garden_id as _active_garden_id,
)
from gardenops.router_helpers import (
    auth_context as _auth_context,
)
from gardenops.router_helpers import (
    require_write as _require_write,
)

router = APIRouter()

ViewType = Literal["plants", "tasks", "journal", "issues", "inventory", "calendar"]


class CreateSavedViewBody(StrictBaseModel):
    view_type: ViewType
    label: str = Field(max_length=100)
    filter_json: dict = Field(default_factory=dict)
    sort_order: int = Field(default=0, ge=0)


class UpdateSavedViewBody(StrictBaseModel):
    label: str | None = Field(default=None, max_length=100)
    filter_json: dict | None = None
    sort_order: int | None = Field(default=None, ge=0)


PRESETS = [
    {
        "view_type": "plants",
        "label": "Missing photos",
        "filter_json": {"missing": "photo"},
        "preset_key": "missing_photos",
    },
    {
        "view_type": "plants",
        "label": "Missing care info",
        "filter_json": {"missing": "care"},
        "preset_key": "missing_care",
    },
    {
        "view_type": "plants",
        "label": "Blooming this month",
        "filter_json": {"bloom_month": "current"},
        "preset_key": "bloom_current",
    },
    {
        "view_type": "tasks",
        "label": "Due this week",
        "filter_json": {"view": "week"},
        "preset_key": "tasks_week",
    },
    {
        "view_type": "tasks",
        "label": "Overdue tasks",
        "filter_json": {"view": "overdue"},
        "preset_key": "tasks_overdue",
    },
    {
        "view_type": "issues",
        "label": "Open issues",
        "filter_json": {"status": "open"},
        "preset_key": "issues_open",
    },
    {
        "view_type": "issues",
        "label": "Follow-up due",
        "filter_json": {"follow_up_due": True},
        "preset_key": "issues_follow_up",
    },
    {
        "view_type": "journal",
        "label": "Recent activity",
        "filter_json": {"date_from": "last_30_days"},
        "preset_key": "journal_recent",
    },
    {
        "view_type": "inventory",
        "label": "Out of stock",
        "filter_json": {"qty_zero": True},
        "preset_key": "inventory_empty",
    },
    {
        "view_type": "calendar",
        "label": "Essential care",
        "filter_json": {
            "preset_key": "essential",
            "visible_sources": [
                "prune",
                "fertilize",
                "sow",
                "plant_out",
                "observe_bloom",
                "harvest",
                "inspect_issue",
                "weather_alert",
            ],
            "include_recent_history": False,
            "view_mode": "month",
        },
        "preset_key": "calendar_essential",
    },
    {
        "view_type": "calendar",
        "label": "High-value work",
        "filter_json": {
            "preset_key": "high_value",
            "visible_sources": [
                "prune",
                "fertilize",
                "sow",
                "plant_out",
                "harvest",
                "inspect_issue",
                "weather_alert",
            ],
            "include_recent_history": False,
            "view_mode": "month",
        },
        "preset_key": "calendar_high_value",
    },
    {
        "view_type": "plants",
        "label": "Harvestable now",
        "filter_json": {"harvestable": True},
        "preset_key": "harvestable_now",
    },
]


def _serialize_view(row: dict[str, Any]) -> dict:
    try:
        filters = json.loads(row["filter_json"] or "{}")
    except (
        json.JSONDecodeError,
        TypeError,
    ):
        filters = {}
    return {
        "id": int(row["id"]),
        "user_id": int(row["user_id"]) if row["user_id"] else None,
        "garden_id": int(row["garden_id"]),
        "view_type": str(row["view_type"]),
        "label": str(row["label"]),
        "filter_json": filters,
        "is_preset": bool(row["is_preset"]),
        "sort_order": int(row["sort_order"]),
        "created_at_ms": int(row["created_at_ms"]),
        "updated_at_ms": int(row["updated_at_ms"]),
    }


@router.get("/saved-views")
def list_saved_views(
    request: Request,
    conn: DB,
    view_type: Annotated[str | None, Query()] = None,
) -> dict:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    user_id = context.user_id

    sql = (
        "SELECT * FROM user_saved_views WHERE (user_id = %s OR user_id IS NULL) AND garden_id = %s"
    )
    params: list = [user_id, garden_id]

    if view_type:
        sql += " AND view_type = %s"
        params.append(view_type)

    sql += " ORDER BY sort_order ASC, label ASC"
    rows = conn.execute(sql, params).fetchall()
    return {"views": [_serialize_view(r) for r in rows]}


@router.get("/saved-views/presets")
def list_presets(request: Request) -> dict:
    _auth_context(request)
    return {"presets": PRESETS}


def _require_saved_view_write_principal(context: Any) -> None:
    if context.user_id is None and context.auth_type != "none":
        raise HTTPException(status_code=403, detail="Saved views require a user session")


@router.post("/saved-views", status_code=201)
def create_saved_view(
    request: Request,
    conn: DB,
    body: CreateSavedViewBody,
) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    user_id = context.user_id
    _require_saved_view_write_principal(context)
    now = current_timestamp_ms()

    filter_str = json.dumps(body.filter_json, ensure_ascii=False)
    row = conn.execute(
        """
        INSERT INTO user_saved_views
            (user_id, garden_id, view_type, label, filter_json,
             is_preset, sort_order, created_at_ms, updated_at_ms)
        VALUES (%s, %s, %s, %s, %s, 0, %s, %s, %s) RETURNING id
        """,
        (
            user_id,
            garden_id,
            body.view_type,
            body.label,
            filter_str,
            body.sort_order,
            now,
            now,
        ),
    ).fetchone()
    assert row is not None
    conn.commit()
    return {"status": "ok", "id": row["id"]}


@router.patch("/saved-views/{view_id}")
def update_saved_view(
    request: Request,
    conn: DB,
    view_id: int,
    body: UpdateSavedViewBody,
) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    user_id = context.user_id

    row = conn.execute("SELECT * FROM user_saved_views WHERE id = %s", (view_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Saved view not found")

    # Verify ownership
    if int(row["garden_id"]) != garden_id:
        raise HTTPException(status_code=404, detail="Saved view not found")
    _require_saved_view_write_principal(context)
    if row["user_id"] is None and context.auth_type != "none":
        raise HTTPException(status_code=403, detail="Cannot modify a shared saved view")
    if row["user_id"] is not None and user_id is not None:
        if int(row["user_id"]) != user_id:
            raise HTTPException(status_code=403, detail="Cannot modify another user's view")

    updates: list[str] = []
    params: list = []

    if body.label is not None:
        updates.append("label = %s")
        params.append(body.label)
    if body.filter_json is not None:
        updates.append("filter_json = %s")
        params.append(json.dumps(body.filter_json, ensure_ascii=False))
    if body.sort_order is not None:
        updates.append("sort_order = %s")
        params.append(body.sort_order)

    if not updates:
        return {"status": "ok"}

    updates.append("updated_at_ms = %s")
    params.append(current_timestamp_ms())
    params.append(view_id)

    conn.execute(
        f"UPDATE user_saved_views SET {', '.join(updates)} WHERE id = %s",
        params,
    )
    conn.commit()
    return {"status": "ok"}


@router.delete("/saved-views/{view_id}")
def delete_saved_view(
    request: Request,
    conn: DB,
    view_id: int,
) -> dict:
    context = _auth_context(request)
    _require_write(context)
    garden_id = _active_garden_id(context)
    user_id = context.user_id

    row = conn.execute("SELECT * FROM user_saved_views WHERE id = %s", (view_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Saved view not found")

    if int(row["garden_id"]) != garden_id:
        raise HTTPException(status_code=404, detail="Saved view not found")
    _require_saved_view_write_principal(context)
    if row["user_id"] is None and context.auth_type != "none":
        raise HTTPException(status_code=403, detail="Cannot delete a shared saved view")
    if row["user_id"] is not None and user_id is not None:
        if int(row["user_id"]) != user_id:
            raise HTTPException(status_code=403, detail="Cannot delete another user's view")

    conn.execute("DELETE FROM user_saved_views WHERE id = %s", (view_id,))
    conn.commit()
    return {"status": "ok"}
