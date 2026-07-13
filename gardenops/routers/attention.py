from __future__ import annotations

import os
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field, StrictBool

from gardenops.db import DB, current_timestamp_ms
from gardenops.models import StrictBaseModel
from gardenops.router_helpers import (
    active_garden_id as _active_garden_id,
)
from gardenops.router_helpers import (
    auth_context as _auth_context,
)
from gardenops.router_helpers import (
    is_local_admin_fallback as _is_local_admin_fallback,
)
from gardenops.router_helpers import (
    require_write as _require_write,
)
from gardenops.services.attention import (
    AttentionService,
    attention_request_clock,
    load_attention_preferences,
    resolve_attention_preferences,
    restore_attention_outcome,
    restore_user_attention_state,
    save_attention_preferences,
    serialize_attention_preferences,
    set_user_attention_state,
)
from gardenops.services.attention.preferences import normalize_attention_preference_payload

router = APIRouter()


class AttentionPreferencesBody(StrictBaseModel):
    preset: Literal["calm", "balanced", "detailed", "custom"] = "balanced"
    rules: dict[str, dict[str, Any]] = Field(default_factory=dict)
    quiet_hours: dict[str, Any] = Field(default_factory=dict)
    show_no_action_history: StrictBool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class AttentionSnoozeBody(StrictBaseModel):
    snoozed_until_ms: int = Field(ge=0)
    reason: str = Field(default="", max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _require_personal_state_user_id(request: Request) -> tuple[int, int]:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    if context.user_id is None:
        raise HTTPException(status_code=403, detail="Authentication required")
    return garden_id, int(context.user_id)


def _require_write_user_id(request: Request) -> tuple[int, int]:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    _require_write(context)
    if context.user_id is None:
        raise HTTPException(status_code=403, detail="Authentication required")
    return garden_id, int(context.user_id)


def _clocked_service() -> tuple[AttentionService, int]:
    now_ms, frozen_date = attention_request_clock(now_ms=current_timestamp_ms())
    return AttentionService(frozen_date=frozen_date), now_ms


def _validated_force_degraded_provider(value: str | None) -> str | None:
    if value is None:
        return None
    if os.environ.get("APP_ENV", "").strip().lower() != "test":
        raise HTTPException(status_code=422, detail="force_degraded_provider is test-only")
    if value != "weather":
        raise HTTPException(status_code=422, detail="Unsupported degraded provider")
    return value


def _require_existing_item(
    db: DB,
    *,
    garden_id: int,
    user_id: int,
    item_id: str,
    now_ms: int,
    service: AttentionService,
) -> None:
    service.require_item(
        db,
        garden_id=garden_id,
        user_id=user_id,
        item_id=item_id,
        now_ms=now_ms,
    )


@router.get("/attention/today")
def get_attention_today(
    request: Request,
    db: DB,
    force_degraded_provider: str | None = Query(default=None),
) -> dict[str, Any]:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    force_degraded = _validated_force_degraded_provider(force_degraded_provider)
    service, now_ms = _clocked_service()
    return service.today(
        db,
        garden_id=garden_id,
        user_id=context.user_id,
        now_ms=now_ms,
        force_degraded_provider=force_degraded,
    )


@router.get("/attention/preferences")
def get_attention_preferences(request: Request, db: DB) -> dict[str, Any]:
    context = _auth_context(request)
    preferences = load_attention_preferences(db, context.user_id)
    return serialize_attention_preferences(preferences)


@router.put("/attention/preferences")
def put_attention_preferences(
    body: AttentionPreferencesBody,
    request: Request,
    db: DB,
) -> dict[str, Any]:
    context = _auth_context(request)
    _active_garden_id(context)
    try:
        (
            preset,
            rules,
            quiet_hours,
            show_no_action_history,
            metadata,
        ) = normalize_attention_preference_payload(
            preset=body.preset,
            rules=body.rules,
            quiet_hours=body.quiet_hours,
            show_no_action_history=body.show_no_action_history,
            metadata=body.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if context.user_id is None:
        if not _is_local_admin_fallback(context):
            raise HTTPException(status_code=403, detail="Authentication required")
        return serialize_attention_preferences(
            resolve_attention_preferences(
                user_id=0,
                legacy_preferences=None,
                saved_attention_preferences={
                    "user_id": 0,
                    "preset": preset,
                    "rules": rules,
                    "quiet_hours": quiet_hours,
                    "show_no_action_history": show_no_action_history,
                    "metadata": metadata,
                },
            )
        )
    user_id = int(context.user_id)
    now_ms = current_timestamp_ms()
    preferences = save_attention_preferences(
        db,
        user_id=user_id,
        preset=preset,
        rules=rules,
        quiet_hours=quiet_hours,
        show_no_action_history=show_no_action_history,
        metadata=metadata,
        now_ms=now_ms,
    )
    db.commit()
    return serialize_attention_preferences(preferences)


@router.post("/attention/items/{item_id}/read")
def read_attention_item(item_id: str, request: Request, db: DB) -> dict[str, str]:
    garden_id, user_id = _require_personal_state_user_id(request)
    service, now_ms = _clocked_service()
    _require_existing_item(
        db,
        garden_id=garden_id,
        user_id=user_id,
        item_id=item_id,
        now_ms=now_ms,
        service=service,
    )
    set_user_attention_state(
        db,
        garden_id=garden_id,
        user_id=user_id,
        item_id=item_id,
        user_state="read",
        now_ms=now_ms,
    )
    db.commit()
    return {"status": "ok"}


@router.post("/attention/items/{item_id}/dismiss")
def dismiss_attention_item(item_id: str, request: Request, db: DB) -> dict[str, str]:
    garden_id, user_id = _require_personal_state_user_id(request)
    service, now_ms = _clocked_service()
    _require_existing_item(
        db,
        garden_id=garden_id,
        user_id=user_id,
        item_id=item_id,
        now_ms=now_ms,
        service=service,
    )
    set_user_attention_state(
        db,
        garden_id=garden_id,
        user_id=user_id,
        item_id=item_id,
        user_state="dismissed",
        now_ms=now_ms,
    )
    db.commit()
    return {"status": "ok"}


@router.post("/attention/items/{item_id}/snooze")
def snooze_attention_item(
    item_id: str,
    body: AttentionSnoozeBody,
    request: Request,
    db: DB,
) -> dict[str, str]:
    garden_id, user_id = _require_personal_state_user_id(request)
    service, now_ms = _clocked_service()
    _require_existing_item(
        db,
        garden_id=garden_id,
        user_id=user_id,
        item_id=item_id,
        now_ms=now_ms,
        service=service,
    )
    set_user_attention_state(
        db,
        garden_id=garden_id,
        user_id=user_id,
        item_id=item_id,
        user_state="snoozed",
        now_ms=now_ms,
        snoozed_until_ms=body.snoozed_until_ms,
        reason=body.reason,
        metadata=body.metadata,
    )
    db.commit()
    return {"status": "ok"}


@router.post("/attention/outcomes/{outcome_id}/restore")
def restore_attention_outcome_item(
    outcome_id: str,
    request: Request,
    db: DB,
) -> dict[str, str]:
    garden_id, user_id = _require_write_user_id(request)
    _service, now_ms = _clocked_service()
    status = restore_attention_outcome(
        db,
        garden_id=garden_id,
        outcome_id=outcome_id,
        user_id=user_id,
        now_ms=now_ms,
    )
    db.commit()
    return {"status": status}


@router.post("/attention/items/{item_id}/restore")
def restore_attention_item(item_id: str, request: Request, db: DB) -> dict[str, str]:
    garden_id, user_id = _require_personal_state_user_id(request)
    service, now_ms = _clocked_service()
    _require_existing_item(
        db,
        garden_id=garden_id,
        user_id=user_id,
        item_id=item_id,
        now_ms=now_ms,
        service=service,
    )
    restore_user_attention_state(
        db,
        garden_id=garden_id,
        user_id=user_id,
        item_id=item_id,
    )
    db.commit()
    return {"status": "ok"}
