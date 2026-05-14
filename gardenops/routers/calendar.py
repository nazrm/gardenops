"""Calendar router -- normalized planning events, preferences, and subscribed feeds."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import Field

from gardenops.branding import app_name, app_slug
from gardenops.db import DB, current_timestamp_ms
from gardenops.feature_gates import feature_allowed
from gardenops.models import StrictBaseModel
from gardenops.rate_limit import enforce_rate_limit
from gardenops.router_helpers import (
    active_garden_id as _active_garden_id,
)
from gardenops.router_helpers import (
    auth_context as _auth_context,
)
from gardenops.router_helpers import (
    effective_role as _effective_role,
)
from gardenops.router_helpers import (
    generate_public_id as _generate_public_id,
)
from gardenops.router_helpers import (
    require_write as _require_write,
)
from gardenops.router_helpers import (
    validate_date as _validate_date,
)
from gardenops.services.calendar_service import (
    build_calendar_ics,
    build_calendar_payload,
    load_calendar_preferences,
    normalize_calendar_preset,
    normalize_calendar_view_mode,
    normalize_selected_plant_ids,
    normalize_selected_plot_ids,
    normalize_selected_zone_codes,
    normalize_visible_sources,
    parse_selected_plant_ids_query,
    parse_selected_plot_ids_query,
    parse_selected_zone_codes_query,
    parse_visible_sources_query,
    preset_definitions,
    source_definitions,
    subscription_window,
    upsert_calendar_preferences,
    validate_calendar_range,
)

router = APIRouter()
feed_router = APIRouter()


class UpdateCalendarPreferencesBody(StrictBaseModel):
    default_view: str | None = None
    selected_preset: str | None = None
    visible_sources: list[str] | None = None
    include_recent_history: bool | None = None
    selected_plant_ids: list[str] | None = None
    selected_plot_ids: list[str] | None = None
    selected_zone_codes: list[str] | None = None


class CreateCalendarSubscriptionBody(StrictBaseModel):
    label: str | None = Field(default=None, max_length=120)
    preset_key: str | None = Field(default="essential", max_length=40)
    visible_sources: list[str] | None = None


class UpsertCalendarManualEventBody(StrictBaseModel):
    title: str = Field(..., min_length=1, max_length=160)
    event_on: str = Field(..., max_length=10)
    description: str | None = Field(default="", max_length=4000)
    plant_ids: list[str] | None = None
    plot_ids: list[str] | None = None


def _calendar_capabilities(request: Request) -> dict[str, bool]:
    context = _auth_context(request)
    role = _effective_role(context)
    can_subscribe = (
        context.user_id is not None
        and feature_allowed(context.subscription_tier, "calendar_subscriptions")
        and role in {"editor", "admin"}
    )
    return {
        "can_subscribe": can_subscribe,
        "can_revoke_all": can_subscribe and role == "admin",
    }


def _garden_name(conn: DB, garden_id: int) -> str:
    row = conn.execute("SELECT name FROM gardens WHERE id = %s", (garden_id,)).fetchone()
    fallback = f"{app_name()} Garden"
    return str(row["name"] or fallback) if row else fallback


def _require_subscription_access(request: Request) -> tuple[Any, int]:
    context = _auth_context(request)
    _require_write(context)
    if context.user_id is None:
        raise HTTPException(
            status_code=403,
            detail="Calendar subscriptions require a signed-in user",
        )
    if _effective_role(context) not in {"editor", "admin"}:
        raise HTTPException(status_code=403, detail="Calendar subscriptions require editor access")
    if not feature_allowed(context.subscription_tier, "calendar_subscriptions"):
        raise HTTPException(status_code=403, detail="Calendar subscriptions are unavailable")
    return context, _active_garden_id(context)


def _require_calendar_event_write(request: Request) -> tuple[Any, int]:
    context = _auth_context(request)
    _require_write(context)
    if context.user_id is None:
        raise HTTPException(status_code=403, detail="Calendar events require a signed-in user")
    return context, _active_garden_id(context)


def _normalize_manual_event_body(
    body: UpsertCalendarManualEventBody,
) -> tuple[str, str, str, list[str], list[str]]:
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Calendar event title is required")
    _validate_date(body.event_on)
    description = (body.description or "").strip()
    plant_ids = normalize_selected_plant_ids(body.plant_ids)
    plot_ids = normalize_selected_plot_ids(body.plot_ids)
    return title, body.event_on, description, plant_ids, plot_ids


def _ensure_calendar_plant_ids(
    conn: DB,
    *,
    garden_id: int,
    plant_ids: list[str],
) -> None:
    if not plant_ids:
        return
    rows = conn.execute(
        """
        SELECT plt_id
        FROM plant_ownership
        WHERE garden_id = %s
          AND plt_id = ANY(%s)
        """,
        (garden_id, plant_ids),
    ).fetchall()
    found = {str(row["plt_id"]) for row in rows}
    missing = [plant_id for plant_id in plant_ids if plant_id not in found]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown plants for active garden: {missing[0]}",
        )


def _ensure_calendar_plot_ids(
    conn: DB,
    *,
    garden_id: int,
    plot_ids: list[str],
) -> None:
    if not plot_ids:
        return
    rows = conn.execute(
        """
        SELECT plot_id
        FROM plot_ownership
        WHERE garden_id = %s
          AND plot_id = ANY(%s)
        """,
        (garden_id, plot_ids),
    ).fetchall()
    found = {str(row["plot_id"]) for row in rows}
    missing = [plot_id for plot_id in plot_ids if plot_id not in found]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown plots for active garden: {missing[0]}",
        )


def _load_calendar_manual_event(
    conn: DB,
    *,
    garden_id: int,
    event_id: str,
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT *
        FROM garden_calendar_events
        WHERE public_id = %s
          AND garden_id = %s
        LIMIT 1
        """,
        (event_id, garden_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Calendar event not found")
    return dict(row)


def _manual_event_payload(
    conn: DB,
    *,
    garden_id: int,
    event_id: str,
    event_on: str,
) -> dict[str, Any]:
    event_date = date.fromisoformat(event_on)
    payload = build_calendar_payload(
        conn,
        garden_id=garden_id,
        start=event_date,
        end=event_date + timedelta(days=1),
        visible_sources=["garden_event"],
        include_recent_history=False,
        selected_plant_ids=[],
        selected_plot_ids=[],
        selected_zone_codes=[],
    )
    for event in payload["events"]:
        if str(event.get("target_id")) == event_id:
            return event
    raise HTTPException(status_code=500, detail="Calendar event payload unavailable")


def _serialize_subscription(
    row: dict[str, Any],
    *,
    context_user_id: int | None,
    can_revoke_all: bool,
) -> dict:
    scope = {}
    try:
        scope = json.loads(row["scope_json"] or "{}")
    except TypeError, json.JSONDecodeError:
        scope = {}
    owner_user_id = int(row["owner_user_id"])
    owned_by_me = context_user_id is not None and owner_user_id == int(context_user_id)
    return {
        "id": str(row["public_id"]),
        "label": str(row["label"]),
        "preset_key": str(row["preset_key"]),
        "visible_sources": normalize_visible_sources(
            scope.get("visible_sources"),
            preset_key=normalize_calendar_preset(str(row["preset_key"])),
        ),
        "token_hint": str(row["token_hint"]),
        "created_at_ms": int(row["created_at_ms"]),
        "updated_at_ms": int(row["updated_at_ms"]),
        "owner_user_id": owner_user_id,
        "owned_by_me": owned_by_me,
        "can_revoke": can_revoke_all or owned_by_me,
    }


@router.get("/calendar/preferences")
def get_calendar_preferences(request: Request, conn: DB) -> dict[str, Any]:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    preferences, persisted = load_calendar_preferences(
        conn,
        user_id=context.user_id,
        garden_id=garden_id,
    )
    return {
        "preferences": preferences,
        "persisted": persisted,
        "available_views": ["month", "week", "agenda"],
        "available_sources": source_definitions(),
        "presets": preset_definitions(),
        "capabilities": _calendar_capabilities(request),
    }


@router.patch("/calendar/preferences")
def update_calendar_preferences(
    request: Request,
    conn: DB,
    body: UpdateCalendarPreferencesBody,
) -> dict[str, Any]:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    if context.user_id is None:
        raise HTTPException(status_code=403, detail="Calendar preferences require a signed-in user")
    current, _persisted = load_calendar_preferences(
        conn,
        user_id=context.user_id,
        garden_id=garden_id,
    )
    selected_preset = normalize_calendar_preset(body.selected_preset or current["selected_preset"])
    visible_sources = normalize_visible_sources(
        body.visible_sources if body.visible_sources is not None else current["visible_sources"],
        preset_key=selected_preset,
    )
    history_enabled = (
        current["include_recent_history"]
        if body.include_recent_history is None
        else bool(body.include_recent_history)
    )
    selected_plant_ids = normalize_selected_plant_ids(
        body.selected_plant_ids
        if body.selected_plant_ids is not None
        else current["selected_plant_ids"],
    )
    selected_plot_ids = normalize_selected_plot_ids(
        body.selected_plot_ids
        if body.selected_plot_ids is not None
        else current["selected_plot_ids"],
    )
    selected_zone_codes = normalize_selected_zone_codes(
        body.selected_zone_codes
        if body.selected_zone_codes is not None
        else current["selected_zone_codes"],
    )
    next_preferences = upsert_calendar_preferences(
        conn,
        user_id=int(context.user_id),
        garden_id=garden_id,
        default_view=normalize_calendar_view_mode(body.default_view or current["default_view"]),
        selected_preset=selected_preset,
        visible_sources=visible_sources,
        include_recent_history=history_enabled,
        selected_plant_ids=selected_plant_ids,
        selected_plot_ids=selected_plot_ids,
        selected_zone_codes=selected_zone_codes,
        now_ms=current_timestamp_ms(),
    )
    conn.commit()
    return {
        "status": "ok",
        "preferences": next_preferences,
    }


@router.get("/calendar/events")
def get_calendar_events(
    request: Request,
    conn: DB,
    start: str = Query(...),
    end: str = Query(...),
    preset: str | None = Query(default=None),
    visible_sources: str | None = Query(default=None),
    include_recent_history: bool | None = Query(default=None),
    selected_plant_ids: str | None = Query(default=None),
    selected_plot_ids: str | None = Query(default=None),
    selected_zone_codes: str | None = Query(default=None),
) -> dict[str, Any]:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    start_date, end_date = validate_calendar_range(start, end)
    preferences, _persisted = load_calendar_preferences(
        conn,
        user_id=context.user_id,
        garden_id=garden_id,
    )
    preset_key = normalize_calendar_preset(preset or preferences["selected_preset"])
    source_keys = (
        parse_visible_sources_query(visible_sources, preset_key=preset_key)
        if visible_sources is not None
        else normalize_visible_sources(preferences["visible_sources"], preset_key=preset_key)
    )
    history_enabled = (
        preferences["include_recent_history"]
        if include_recent_history is None
        else bool(include_recent_history)
    )
    selected_plant_id_list = (
        parse_selected_plant_ids_query(selected_plant_ids)
        if selected_plant_ids is not None
        else normalize_selected_plant_ids(preferences["selected_plant_ids"])
    )
    selected_plot_id_list = (
        parse_selected_plot_ids_query(selected_plot_ids)
        if selected_plot_ids is not None
        else normalize_selected_plot_ids(preferences["selected_plot_ids"])
    )
    selected_zone_code_list = (
        parse_selected_zone_codes_query(selected_zone_codes)
        if selected_zone_codes is not None
        else normalize_selected_zone_codes(preferences["selected_zone_codes"])
    )
    payload = build_calendar_payload(
        conn,
        garden_id=garden_id,
        start=start_date,
        end=end_date,
        visible_sources=source_keys,
        include_recent_history=history_enabled,
        selected_plant_ids=selected_plant_id_list,
        selected_plot_ids=selected_plot_id_list,
        selected_zone_codes=selected_zone_code_list,
    )
    payload.update(
        {
            "selected_preset": preset_key,
            "visible_sources": source_keys,
            "include_recent_history": history_enabled,
            "selected_plant_ids": selected_plant_id_list,
            "selected_plot_ids": selected_plot_id_list,
            "selected_zone_codes": selected_zone_code_list,
        }
    )
    return payload


@router.get("/calendar/export.ics")
def export_calendar_ics(
    request: Request,
    conn: DB,
    start: str = Query(...),
    end: str = Query(...),
    preset: str | None = Query(default=None),
    visible_sources: str | None = Query(default=None),
    include_recent_history: bool | None = Query(default=None),
    selected_plant_ids: str | None = Query(default=None),
    selected_plot_ids: str | None = Query(default=None),
    selected_zone_codes: str | None = Query(default=None),
) -> Response:
    enforce_rate_limit(request, bucket="calendar_export", limit=20, window_seconds=60)
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    start_date, end_date = validate_calendar_range(start, end)
    preferences, _persisted = load_calendar_preferences(
        conn,
        user_id=context.user_id,
        garden_id=garden_id,
    )
    preset_key = normalize_calendar_preset(preset or preferences["selected_preset"])
    source_keys = (
        parse_visible_sources_query(visible_sources, preset_key=preset_key)
        if visible_sources is not None
        else normalize_visible_sources(preferences["visible_sources"], preset_key=preset_key)
    )
    selected_plant_id_list = (
        parse_selected_plant_ids_query(selected_plant_ids)
        if selected_plant_ids is not None
        else normalize_selected_plant_ids(preferences["selected_plant_ids"])
    )
    selected_plot_id_list = (
        parse_selected_plot_ids_query(selected_plot_ids)
        if selected_plot_ids is not None
        else normalize_selected_plot_ids(preferences["selected_plot_ids"])
    )
    selected_zone_code_list = (
        parse_selected_zone_codes_query(selected_zone_codes)
        if selected_zone_codes is not None
        else normalize_selected_zone_codes(preferences["selected_zone_codes"])
    )
    history_enabled = (
        preferences["include_recent_history"]
        if include_recent_history is None
        else bool(include_recent_history)
    )
    payload = build_calendar_payload(
        conn,
        garden_id=garden_id,
        start=start_date,
        end=end_date,
        visible_sources=source_keys,
        include_recent_history=history_enabled,
        selected_plant_ids=selected_plant_id_list,
        selected_plot_ids=selected_plot_id_list,
        selected_zone_codes=selected_zone_code_list,
    )
    ics, etag, last_modified = build_calendar_ics(
        garden_name=_garden_name(conn, garden_id),
        events=payload["events"],
    )
    headers = {
        "Content-Disposition": f'attachment; filename="{app_slug()}-calendar.ics"',
        "Cache-Control": "no-store",
        "ETag": etag,
    }
    if last_modified:
        headers["Last-Modified"] = last_modified
    return Response(ics, media_type="text/calendar; charset=utf-8", headers=headers)


@router.post("/calendar/manual-events", status_code=201)
def create_calendar_manual_event(
    request: Request,
    conn: DB,
    body: UpsertCalendarManualEventBody,
) -> dict[str, Any]:
    context, garden_id = _require_calendar_event_write(request)
    title, event_on, description, plant_ids, plot_ids = _normalize_manual_event_body(body)
    _ensure_calendar_plant_ids(conn, garden_id=garden_id, plant_ids=plant_ids)
    _ensure_calendar_plot_ids(conn, garden_id=garden_id, plot_ids=plot_ids)
    public_id = _generate_public_id("calevt")
    now_ms = current_timestamp_ms()
    row = conn.execute(
        """
        INSERT INTO garden_calendar_events
            (public_id, garden_id, title, description, event_on, created_by_user_id,
             updated_by_user_id, created_at_ms, updated_at_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            public_id,
            garden_id,
            title,
            description,
            event_on,
            int(context.user_id),
            int(context.user_id),
            now_ms,
            now_ms,
        ),
    ).fetchone()
    assert row is not None
    event_pk = int(row["id"])
    for plant_id in plant_ids:
        conn.execute(
            """
            INSERT INTO garden_calendar_event_plants (event_id, plt_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (event_pk, plant_id),
        )
    for plot_id in plot_ids:
        conn.execute(
            """
            INSERT INTO garden_calendar_event_plots (event_id, plot_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (event_pk, plot_id),
        )
    conn.commit()
    return {
        "status": "created",
        "event": _manual_event_payload(
            conn,
            garden_id=garden_id,
            event_id=public_id,
            event_on=event_on,
        ),
    }


@router.patch("/calendar/manual-events/{event_id}")
def update_calendar_manual_event(
    request: Request,
    conn: DB,
    event_id: str,
    body: UpsertCalendarManualEventBody,
) -> dict[str, Any]:
    context, garden_id = _require_calendar_event_write(request)
    existing = _load_calendar_manual_event(conn, garden_id=garden_id, event_id=event_id)
    title, event_on, description, plant_ids, plot_ids = _normalize_manual_event_body(body)
    _ensure_calendar_plant_ids(conn, garden_id=garden_id, plant_ids=plant_ids)
    _ensure_calendar_plot_ids(conn, garden_id=garden_id, plot_ids=plot_ids)
    now_ms = current_timestamp_ms()
    conn.execute(
        """
        UPDATE garden_calendar_events
        SET title = %s,
            description = %s,
            event_on = %s,
            updated_by_user_id = %s,
            updated_at_ms = %s
        WHERE public_id = %s
          AND garden_id = %s
        """,
        (
            title,
            description,
            event_on,
            int(context.user_id),
            now_ms,
            event_id,
            garden_id,
        ),
    )
    conn.execute(
        "DELETE FROM garden_calendar_event_plants WHERE event_id = %s",
        (int(existing["id"]),),
    )
    for plant_id in plant_ids:
        conn.execute(
            """
            INSERT INTO garden_calendar_event_plants (event_id, plt_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (int(existing["id"]), plant_id),
        )
    conn.execute(
        "DELETE FROM garden_calendar_event_plots WHERE event_id = %s",
        (int(existing["id"]),),
    )
    for plot_id in plot_ids:
        conn.execute(
            """
            INSERT INTO garden_calendar_event_plots (event_id, plot_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (int(existing["id"]), plot_id),
        )
    conn.commit()
    return {
        "status": "updated",
        "event": _manual_event_payload(
            conn,
            garden_id=garden_id,
            event_id=event_id,
            event_on=event_on,
        ),
    }


@router.delete("/calendar/manual-events/{event_id}")
def delete_calendar_manual_event(
    request: Request,
    conn: DB,
    event_id: str,
) -> dict[str, Any]:
    _context, garden_id = _require_calendar_event_write(request)
    _existing = _load_calendar_manual_event(conn, garden_id=garden_id, event_id=event_id)
    conn.execute(
        """
        DELETE FROM garden_calendar_events
        WHERE public_id = %s
          AND garden_id = %s
        """,
        (event_id, garden_id),
    )
    conn.commit()
    return {"status": "deleted", "id": event_id}


@router.get("/calendar/subscriptions")
def list_calendar_subscriptions(request: Request, conn: DB) -> dict[str, Any]:
    context, garden_id = _require_subscription_access(request)
    role = _effective_role(context)
    params: list[Any] = [garden_id]
    sql = """
        SELECT *
        FROM calendar_subscriptions
        WHERE garden_id = %s
          AND revoked_at_ms IS NULL
    """
    if role != "admin":
        sql += " AND owner_user_id = %s"
        params.append(int(context.user_id))
    sql += " ORDER BY created_at_ms DESC"
    rows = conn.execute(sql, params).fetchall()
    capabilities = _calendar_capabilities(request)
    return {
        "subscriptions": [
            _serialize_subscription(
                dict(row),
                context_user_id=context.user_id,
                can_revoke_all=capabilities["can_revoke_all"],
            )
            for row in rows
        ]
    }


@router.post("/calendar/subscriptions", status_code=201)
def create_calendar_subscription(
    request: Request,
    conn: DB,
    body: CreateCalendarSubscriptionBody,
) -> dict[str, Any]:
    context, garden_id = _require_subscription_access(request)
    preset_key = normalize_calendar_preset(body.preset_key or "essential")
    source_keys = normalize_visible_sources(body.visible_sources, preset_key=preset_key)
    now_ms = current_timestamp_ms()
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    token_hint = f"...{token[-6:]}"
    label = (
        body.label.strip()
        if body.label and body.label.strip()
        else f"{preset_key.replace('_', ' ').title()} feed"
    )
    public_id = _generate_public_id("calsub")
    conn.execute(
        """
        INSERT INTO calendar_subscriptions
            (public_id, garden_id, owner_user_id, created_by_user_id, label, preset_key,
             token_hash, token_hint, scope_json, created_at_ms, updated_at_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            public_id,
            garden_id,
            int(context.user_id),
            int(context.user_id),
            label,
            preset_key,
            token_hash,
            token_hint,
            json.dumps({"visible_sources": source_keys}, ensure_ascii=False),
            now_ms,
            now_ms,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM calendar_subscriptions WHERE public_id = %s LIMIT 1",
        (public_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=500, detail="Failed to create calendar subscription")
    capabilities = _calendar_capabilities(request)
    return {
        "status": "ok",
        "subscription": _serialize_subscription(
            dict(row),
            context_user_id=context.user_id,
            can_revoke_all=capabilities["can_revoke_all"],
        ),
        "feed_path": f"/calendar/subscriptions/{token}.ics",
    }


@router.delete("/calendar/subscriptions/{subscription_id}")
def revoke_calendar_subscription(
    request: Request,
    conn: DB,
    subscription_id: str,
) -> dict[str, Any]:
    context, garden_id = _require_subscription_access(request)
    row = conn.execute(
        """
        SELECT *
        FROM calendar_subscriptions
        WHERE public_id = %s
          AND garden_id = %s
          AND revoked_at_ms IS NULL
        LIMIT 1
        """,
        (subscription_id, garden_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Calendar subscription not found")
    role = _effective_role(context)
    owner_user_id = int(row["owner_user_id"])
    if role != "admin" and int(context.user_id) != owner_user_id:
        raise HTTPException(status_code=403, detail="Cannot revoke another user's calendar feed")
    now_ms = current_timestamp_ms()
    conn.execute(
        """
        UPDATE calendar_subscriptions
        SET revoked_at_ms = %s,
            updated_at_ms = %s
        WHERE public_id = %s
        """,
        (now_ms, now_ms, subscription_id),
    )
    conn.commit()
    return {"status": "revoked", "id": subscription_id}


@feed_router.get("/calendar/subscriptions/{token}.ics")
def get_calendar_subscription_feed(
    token: str,
    request: Request,
    conn: DB,
) -> Response:
    enforce_rate_limit(request, bucket="calendar_subscription_feed", limit=60, window_seconds=60)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = conn.execute(
        """
        SELECT s.*, u.subscription_tier
        FROM calendar_subscriptions s
        JOIN auth_users u
          ON u.id = s.owner_user_id
         AND u.is_active = 1
        JOIN garden_memberships gm
          ON gm.garden_id = s.garden_id
         AND gm.user_id = s.owner_user_id
         AND gm.role IN ('editor', 'admin')
        WHERE s.token_hash = %s
          AND s.revoked_at_ms IS NULL
        LIMIT 1
        """,
        (token_hash,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Calendar feed not found")
    if not feature_allowed(str(row["subscription_tier"] or "home"), "calendar_subscriptions"):
        raise HTTPException(status_code=404, detail="Calendar feed not found")
    scope = {}
    try:
        scope = json.loads(row["scope_json"] or "{}")
    except TypeError, json.JSONDecodeError:
        scope = {}
    preset_key = normalize_calendar_preset(str(row["preset_key"] or "essential"))
    source_keys = normalize_visible_sources(scope.get("visible_sources"), preset_key=preset_key)
    start_date, end_date = subscription_window(date.today())
    payload = build_calendar_payload(
        conn,
        garden_id=int(row["garden_id"]),
        start=start_date,
        end=end_date,
        visible_sources=source_keys,
        include_recent_history=False,
        selected_plant_ids=[],
        selected_plot_ids=[],
        selected_zone_codes=[],
    )
    ics, etag, last_modified = build_calendar_ics(
        garden_name=_garden_name(conn, int(row["garden_id"])),
        events=payload["events"],
    )
    if request.headers.get("if-none-match", "").strip() == etag:
        headers = {
            "Cache-Control": "private, max-age=300, must-revalidate",
            "ETag": etag,
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
        }
        if last_modified:
            headers["Last-Modified"] = last_modified
        return Response(status_code=304, headers=headers)
    headers = {
        "Cache-Control": "private, max-age=300, must-revalidate",
        "ETag": etag,
        "Referrer-Policy": "no-referrer",
        "X-Robots-Tag": "noindex, nofollow",
        "Content-Disposition": f'inline; filename="{app_slug()}-calendar.ics"',
    }
    if last_modified:
        headers["Last-Modified"] = last_modified
    return Response(ics, media_type="text/calendar; charset=utf-8", headers=headers)
