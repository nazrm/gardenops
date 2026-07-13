"""Notification router – CRUD for in-app notification events and user prefs."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field, StrictBool, field_validator

from gardenops.db import DB
from gardenops.feature_gates import feature_allowed
from gardenops.models import StrictBaseModel
from gardenops.rate_limit import enforce_rate_limit, env_int
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
    is_local_admin_fallback as _is_local_admin_fallback,
)
from gardenops.router_helpers import (
    require_write as _require_write,
)
from gardenops.services.attention import (
    load_attention_preferences,
    merge_notification_preferences,
    notification_quiet_hours_from_attention,
    notification_rules_from_attention,
    save_attention_preferences,
)
from gardenops.services.notification_service import (
    clear_expired_notifications,
    clear_stale_informational_notifications,
    clear_stale_task_notifications,
    create_task_due_notifications,
    deliver_pending_email_digests,
    dismiss_notification,
    get_unread_count,
    mark_all_read,
    mark_read,
    normalize_notification_rules,
    notification_policy_catalog,
    notification_request_clock,
    notification_rows_allowed_for_user,
    notification_rules_json,
    run_notification_maintenance_for_garden,
    validate_notification_rules,
)

router = APIRouter()


# ── Pydantic models ───────────────────────────────────────

_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*$",
)


class NotificationPreferencesBody(StrictBaseModel):
    in_app_enabled: StrictBool = True
    email_enabled: StrictBool = False
    email_address: str = Field(default="", max_length=320)
    digest_frequency: Literal["none", "daily", "weekly"] = "daily"
    quiet_hours_json: dict = Field(default_factory=dict)
    task_due_enabled: StrictBool = True
    task_overdue_enabled: StrictBool = True
    notification_rules: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @field_validator("email_address")
    @classmethod
    def validate_email_format(cls, v: str) -> str:
        if not v:
            return v
        if not _EMAIL_RE.match(v):
            msg = "Invalid email address format"
            raise ValueError(msg)
        return v


# ── Serialization helpers ─────────────────────────────────


def _serialize_notification(row: dict[str, Any], *, now_ms: int | None = None) -> dict:
    clear_reason = str(row["clear_reason"]) if row.get("clear_reason") else None
    cleared_at_ms = int(row["cleared_at_ms"]) if row.get("cleared_at_ms") else None
    expires_at_ms = int(row["expires_at_ms"]) if row.get("expires_at_ms") else None
    if clear_reason is None and expires_at_ms is not None and now_ms is not None:
        if expires_at_ms < now_ms:
            clear_reason = "expired"
            cleared_at_ms = expires_at_ms
    return {
        "id": str(row["public_id"]),
        "garden_id": int(row["garden_id"]),
        "user_id": int(row["user_id"]) if row["user_id"] else None,
        "notification_type": str(row["notification_type"]),
        "notification_subtype": (
            str(row["notification_subtype"]) if row.get("notification_subtype") else None
        ),
        "severity": str(row["severity"] or "normal"),
        "title": str(row["title"]),
        "body": str(row["body"]),
        "target_type": str(row["target_type"]) if row["target_type"] else None,
        "target_id": str(row["target_id"]) if row["target_id"] else None,
        "read_at_ms": int(row["read_at_ms"]) if row["read_at_ms"] else None,
        "dismissed": bool(row["dismissed"]),
        "expires_at_ms": expires_at_ms,
        "cleared_at_ms": cleared_at_ms,
        "clear_reason": clear_reason,
        "created_at_ms": int(row["created_at_ms"]),
        "metadata": json.loads(str(row["metadata_json"])) if row["metadata_json"] else None,
    }


def _serialize_preferences(row: dict[str, Any] | None) -> dict:
    if row is None:
        rules = normalize_notification_rules(None)
        return {
            "in_app_enabled": True,
            "email_enabled": False,
            "email_address": "",
            "digest_frequency": "daily",
            "quiet_hours_json": {},
            "task_due_enabled": True,
            "task_overdue_enabled": True,
            "notification_rules": rules,
            "policy": notification_policy_catalog(),
        }
    import json

    qh = row["quiet_hours_json"]
    try:
        qh_parsed = json.loads(qh) if qh else {}
    except (
        json.JSONDecodeError,
        TypeError,
    ):
        qh_parsed = {}
    raw_rules = row["rules_json"] if "rules_json" in row else "{}"
    try:
        rules_payload = json.loads(raw_rules) if raw_rules else {}
    except (
        json.JSONDecodeError,
        TypeError,
    ):
        rules_payload = {}
    rules = normalize_notification_rules(rules_payload)
    if not rules_payload:
        rules["task_due"]["in_app_enabled"] = bool(row["task_due_enabled"])
        rules["task_overdue"]["in_app_enabled"] = bool(row["task_overdue_enabled"])
    return {
        "in_app_enabled": bool(row["in_app_enabled"]),
        "email_enabled": bool(row["email_enabled"]),
        "email_address": str(row["email_address"]),
        "digest_frequency": str(row["digest_frequency"]),
        "quiet_hours_json": qh_parsed,
        "task_due_enabled": bool(row["task_due_enabled"]),
        "task_overdue_enabled": bool(row["task_overdue_enabled"]),
        "notification_rules": rules,
        "policy": notification_policy_catalog(),
    }


def _require_email_notifications_feature(context) -> None:
    if not feature_allowed(context.subscription_tier, "email_notifications"):
        raise HTTPException(status_code=403, detail="Email notifications require a Pro plan")


def _enables_email_rule(
    submitted_rules: dict[str, dict[str, Any]],
    current_rules: dict[str, Any],
) -> bool:
    """Return whether an explicit rule change turns email delivery on."""
    for key, submitted_rule in submitted_rules.items():
        if not isinstance(submitted_rule, dict) or "email_enabled" not in submitted_rule:
            continue
        if not bool(submitted_rule["email_enabled"]):
            continue
        current_rule = current_rules.get(key)
        if not isinstance(current_rule, dict) or not bool(current_rule.get("email_enabled", False)):
            return True
    return False


def _require_notification_delivery_admin(context) -> None:
    if _effective_role(context) != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    _require_email_notifications_feature(context)


# ── Endpoints ─────────────────────────────────────────────


@router.get("/notifications")
def list_notifications(
    request: Request,
    db: DB,
    unread_only: bool = Query(default=False),
    scope: Literal["inbox", "log"] = Query(default="inbox"),
    notification_type: str | None = Query(default=None),
    notification_subtype: str | None = Query(default=None),
    clear_reason: str | None = Query(default=None),
    date_from_ms: int | None = Query(default=None, ge=0),
    date_to_ms: int | None = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    include_total: bool = Query(default=True),
) -> dict:
    """List notifications for current user in active garden."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    user_id = context.user_id

    now, frozen_date = notification_request_clock()
    expired = clear_expired_notifications(db, garden_id=garden_id, user_id=user_id, now_ms=now)
    stale_tasks = clear_stale_task_notifications(
        db,
        garden_id=garden_id,
        user_id=user_id,
        today_iso=frozen_date,
        now_ms=now,
    )
    stale_info = clear_stale_informational_notifications(
        db,
        garden_id=garden_id,
        user_id=user_id,
        today_iso=frozen_date,
        now_ms=now,
    )
    if expired or stale_tasks or stale_info:
        db.commit()

    conditions = ["garden_id = %s"]
    params: list = [garden_id]

    if user_id is not None:
        conditions.append("user_id = %s")
        params.append(user_id)

    if scope == "inbox":
        conditions.append("dismissed = 0")
        conditions.append("cleared_at_ms IS NULL")
        conditions.append("(expires_at_ms IS NULL OR expires_at_ms >= %s)")
        params.append(now)

    if unread_only:
        conditions.append("read_at_ms IS NULL")
    if notification_type:
        conditions.append("notification_type = %s")
        params.append(notification_type)
    if notification_subtype:
        conditions.append("notification_subtype = %s")
        params.append(notification_subtype)
    if clear_reason:
        conditions.append("clear_reason = %s")
        params.append(clear_reason)
    if date_from_ms is not None:
        conditions.append("created_at_ms >= %s")
        params.append(date_from_ms)
    if date_to_ms is not None:
        conditions.append("created_at_ms <= %s")
        params.append(date_to_ms)

    where = " AND ".join(conditions)

    if scope == "inbox" and user_id is not None:
        candidate_rows = db.execute(
            f"SELECT * FROM notification_events WHERE {where} "  # noqa: S608
            "ORDER BY created_at_ms DESC",
            params,
        ).fetchall()
        filtered_rows = notification_rows_allowed_for_user(
            db,
            [dict(row) for row in candidate_rows],
            surface="inbox",
            garden_id=garden_id,
            user_id=user_id,
            now_ms=now,
        )
        rows = filtered_rows[offset : offset + limit]
        total = len(filtered_rows)
    else:
        rows = db.execute(
            f"SELECT * FROM notification_events WHERE {where} "  # noqa: S608
            "ORDER BY created_at_ms DESC LIMIT %s OFFSET %s",
            [*params, limit, offset],
        ).fetchall()
        total = None

    response: dict[str, Any] = {
        "notifications": [_serialize_notification(dict(r), now_ms=now) for r in rows],
    }
    if include_total:
        if total is not None:
            response["total"] = total
        else:
            total_row = db.execute(
                f"SELECT COUNT(*) AS c FROM notification_events WHERE {where}",  # noqa: S608
                params,
            ).fetchone()
            response["total"] = total_row["c"] if total_row else 0
    return response


@router.get("/notifications/count")
def notification_count(request: Request, db: DB) -> dict:
    """Get unread notification count for badge."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    now, frozen_date = notification_request_clock()
    expired = clear_expired_notifications(
        db,
        garden_id=garden_id,
        user_id=context.user_id,
        now_ms=now,
    )
    stale_tasks = clear_stale_task_notifications(
        db,
        garden_id=garden_id,
        user_id=context.user_id,
        today_iso=frozen_date,
        now_ms=now,
    )
    stale_info = clear_stale_informational_notifications(
        db,
        garden_id=garden_id,
        user_id=context.user_id,
        today_iso=frozen_date,
        now_ms=now,
    )
    if expired or stale_tasks or stale_info:
        db.commit()
    count = get_unread_count(db, garden_id, context.user_id, now_ms=now)
    return {"count": count}


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: str,
    request: Request,
    db: DB,
) -> dict:
    """Mark a single notification as read."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    now, _ = notification_request_clock()
    updated = mark_read(db, notification_id, context.user_id, garden_id, now_ms=now)
    if not updated:
        raise HTTPException(
            status_code=404,
            detail="Notification not found or already read",
        )
    return {"status": "ok"}


@router.post("/notifications/read-all")
def mark_all_notifications_read(request: Request, db: DB) -> dict:
    """Mark all notifications as read for current user in active garden."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    now, _ = notification_request_clock()
    count = mark_all_read(db, garden_id, context.user_id, now_ms=now)
    return {"status": "ok", "updated": count}


@router.delete("/notifications/{notification_id}")
def dismiss_notification_endpoint(
    notification_id: str,
    request: Request,
    db: DB,
) -> dict:
    """Dismiss (soft-delete) a notification."""
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    now, _ = notification_request_clock()
    updated = dismiss_notification(db, notification_id, context.user_id, garden_id, now_ms=now)
    if not updated:
        raise HTTPException(
            status_code=404,
            detail="Notification not found or already dismissed",
        )
    return {"status": "ok"}


@router.get("/notifications/preferences")
def get_notification_preferences(request: Request, db: DB) -> dict:
    """Get user's notification preferences (returns defaults if none set)."""
    context = _auth_context(request)
    user_id = context.user_id

    if user_id is None:
        return _serialize_preferences(None)
    row = db.execute(
        "SELECT * FROM user_notification_preferences WHERE user_id = %s",
        (user_id,),
    ).fetchone()

    serialized = _serialize_preferences(row)
    saved_attention = db.execute(
        "SELECT 1 FROM user_attention_preferences WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    if saved_attention is None:
        return serialized
    attention_preferences = load_attention_preferences(db, user_id)
    for key, projection in notification_rules_from_attention(attention_preferences).items():
        if key in serialized["notification_rules"]:
            serialized["notification_rules"][key].update(projection)
    digest_quiet_hours = notification_quiet_hours_from_attention(attention_preferences)
    if digest_quiet_hours is not None:
        serialized["quiet_hours_json"] = digest_quiet_hours
    serialized["task_due_enabled"] = bool(
        serialized["notification_rules"]["task_due"]["in_app_enabled"]
    )
    serialized["task_overdue_enabled"] = bool(
        serialized["notification_rules"]["task_overdue"]["in_app_enabled"]
    )
    return serialized


@router.put("/notifications/preferences")
def update_notification_preferences(
    body: NotificationPreferencesBody,
    request: Request,
    db: DB,
) -> dict:
    """Update user's notification preferences (upsert)."""
    import json

    context = _auth_context(request)
    user_id = context.user_id

    if user_id is None and not _is_local_admin_fallback(context):
        raise HTTPException(status_code=403, detail="Authentication required")

    if user_id is None:
        return {"status": "ok"}

    existing_preference = db.execute(
        "SELECT * FROM user_notification_preferences WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    saved_attention = db.execute(
        "SELECT 1 FROM user_attention_preferences WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    existing_attention = load_attention_preferences(db, user_id)
    current_rules = _serialize_preferences(existing_preference)["notification_rules"]
    try:
        validate_notification_rules(body.notification_rules)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if saved_attention is not None:
        for key, projection in notification_rules_from_attention(existing_attention).items():
            if key in current_rules:
                current_rules[key].update(projection)
    if body.email_enabled or _enables_email_rule(body.notification_rules, current_rules):
        _require_email_notifications_feature(context)

    now, _ = notification_request_clock()
    qh_json = json.dumps(body.quiet_hours_json) if body.quiet_hours_json else "{}"
    notification_rule_keys = {
        str(key) for key, rule in body.notification_rules.items() if isinstance(rule, dict)
    }
    rules = normalize_notification_rules(body.notification_rules)
    if not body.notification_rules:
        rules["task_due"]["in_app_enabled"] = bool(body.task_due_enabled)
        rules["task_overdue"]["in_app_enabled"] = bool(body.task_overdue_enabled)
        notification_rule_keys = {"task_due", "task_overdue"}
    rules_json = notification_rules_json(rules)
    task_due_enabled = bool(rules["task_due"]["in_app_enabled"])
    task_overdue_enabled = bool(rules["task_overdue"]["in_app_enabled"])

    db.execute(
        """
        INSERT INTO user_notification_preferences
            (user_id, in_app_enabled, email_enabled, email_address,
             digest_frequency, quiet_hours_json,
             task_due_enabled, task_overdue_enabled, rules_json,
             created_at_ms, updated_at_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT(user_id) DO UPDATE SET
            in_app_enabled = excluded.in_app_enabled,
            email_enabled = excluded.email_enabled,
            email_address = excluded.email_address,
            digest_frequency = excluded.digest_frequency,
            quiet_hours_json = excluded.quiet_hours_json,
            task_due_enabled = excluded.task_due_enabled,
            task_overdue_enabled = excluded.task_overdue_enabled,
            rules_json = excluded.rules_json,
            updated_at_ms = excluded.updated_at_ms
        """,
        (
            user_id,
            int(body.in_app_enabled),
            int(body.email_enabled),
            body.email_address,
            body.digest_frequency,
            qh_json,
            int(task_due_enabled),
            int(task_overdue_enabled),
            rules_json,
            now,
            now,
        ),
    )
    try:
        synchronized_attention = merge_notification_preferences(
            existing_attention,
            notification_rules=rules,
            quiet_hours=body.quiet_hours_json,
            notification_rule_keys=notification_rule_keys,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    save_attention_preferences(
        db,
        user_id=user_id,
        preset=synchronized_attention.preset,
        rules=synchronized_attention.rules,
        quiet_hours=synchronized_attention.quiet_hours,
        show_no_action_history=synchronized_attention.show_no_action_history,
        metadata=synchronized_attention.metadata,
        now_ms=now,
    )
    db.commit()
    return {"status": "ok"}


@router.post("/notifications/generate")
def generate_notifications(request: Request, db: DB) -> dict:
    """Manually trigger task-due notification generation."""
    context = _auth_context(request)
    _require_write(context)
    enforce_rate_limit(
        request,
        bucket="notification-generate",
        limit=env_int("NOTIFICATION_GENERATE_RATE_LIMIT", 5),
        window_seconds=60,
    )
    garden_id = _active_garden_id(context)
    now, _ = notification_request_clock()
    result = create_task_due_notifications(db, garden_id, now_ms=now)
    return result


@router.post("/notifications/process-delivery")
def process_notification_delivery(request: Request, db: DB) -> dict:
    """Process pending email digests for users with email notifications enabled."""
    context = _auth_context(request)
    _require_write(context)
    _require_notification_delivery_admin(context)
    enforce_rate_limit(
        request,
        bucket="notification-delivery",
        limit=env_int("NOTIFICATION_DELIVERY_RATE_LIMIT", 5),
        window_seconds=60,
    )
    garden_id = _active_garden_id(context)
    now, _ = notification_request_clock()
    return deliver_pending_email_digests(db, garden_id, now_ms=now)


@router.post("/notifications/run-maintenance")
def run_notification_maintenance(request: Request, db: DB) -> dict:
    """Run notification generation + email delivery for the active garden."""
    context = _auth_context(request)
    _require_write(context)
    _require_notification_delivery_admin(context)
    enforce_rate_limit(
        request,
        bucket="notification-maintenance",
        limit=env_int("NOTIFICATION_MAINTENANCE_RATE_LIMIT", 3),
        window_seconds=60,
    )
    garden_id = _active_garden_id(context)
    now, _ = notification_request_clock()
    return run_notification_maintenance_for_garden(db, garden_id=garden_id, now_ms=now)
