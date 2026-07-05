from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Protocol, cast
from urllib.parse import urlsplit

from psycopg.conninfo import conninfo_to_dict

AttentionCategory = Literal["needs_action", "warning", "upcoming", "no_action_needed", "system"]
AttentionSeverity = Literal["low", "normal", "high", "critical"]
AttentionDomainState = Literal[
    "active",
    "completed",
    "skipped",
    "dismissed",
    "expired",
    "superseded",
    "no_action_needed",
]
AttentionUserState = Literal["unread", "read", "dismissed", "snoozed", "preference_hidden"]
AttentionDelivery = Literal["panel_only", "inbox", "digest", "interruptive"]
AttentionProviderKey = Literal["task", "weather", "issue", "calendar", "notification_status"]

RAIN_COVERS_WATERING_MM = 10.0
NO_ACTION_RETENTION_DAYS = 30
SEVERITY_RANK: dict[AttentionSeverity, int] = {
    "low": 0,
    "normal": 1,
    "high": 2,
    "critical": 3,
}


_ALLOWED_E2E_HOSTS = {"localhost", "127.0.0.1", "::1"}
_ALLOWED_E2E_HOSTADDRS = {"127.0.0.1", "::1"}
_ALLOWED_E2E_SOCKET_DIRS = {"/var/run/postgresql"}


@dataclass(frozen=True)
class AttentionAction:
    kind: Literal[
        "open_task",
        "open_issue",
        "open_weather",
        "focus_plant",
        "select_plot",
        "open_attention_detail",
        "restore_attention_outcome",
    ]
    label: str
    target_type: str
    target_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AttentionItem:
    id: str
    provider: AttentionProviderKey
    type: str
    category: AttentionCategory
    severity: AttentionSeverity
    title: str
    body: str
    reason: str
    target_type: str | None
    target_id: str | None
    garden_id: int
    audience_user_id: int
    plant_ids: tuple[str, ...] = ()
    plot_ids: tuple[str, ...] = ()
    due_on: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    domain_state: AttentionDomainState = "active"
    user_state: AttentionUserState = "unread"
    lifecycle_scope: Literal["domain", "user"] = "domain"
    delivery_eligibility: tuple[AttentionDelivery, ...] = ("panel_only",)
    rank: int = 500
    group_key: str | None = None
    primary_action: AttentionAction | None = None
    secondary_actions: tuple[AttentionAction, ...] = ()
    explanation: str = ""
    source_label: str = ""
    updated_at_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class AttentionProvider(Protocol):
    key: AttentionProviderKey

    def collect(
        self, conn: Any, *, garden_id: int, user_id: int, now_ms: int
    ) -> list[AttentionItem]: ...


def stable_group_id(provider: str, group_key: str, child_ids: list[str]) -> str:
    digest = hashlib.sha256("|".join(sorted(child_ids)).encode("utf-8")).hexdigest()[:16]
    return f"attn:group:{provider}:{group_key}:{digest}"


def is_generated_watering_task(task_type: str, rule_source: str | None) -> bool:
    value = (rule_source or "").strip()
    return task_type == "water" and (
        value.startswith("water:") or value.startswith("auto:dry_water:")
    )


def normalize_severity(value: str | None) -> AttentionSeverity:
    lowered = (value or "normal").strip().lower()
    if lowered in SEVERITY_RANK:
        return cast(AttentionSeverity, lowered)
    return "normal"


def attention_request_clock(*, now_ms: int) -> tuple[int, str | None]:
    frozen_now = os.environ.get("GARDENOPS_ATTENTION_FROZEN_NOW_MS", "").strip()
    frozen_date = os.environ.get("GARDENOPS_ATTENTION_FROZEN_DATE", "").strip()
    if frozen_now or frozen_date:
        if os.environ.get("APP_ENV", "").strip().lower() != "test":
            raise RuntimeError("Attention frozen clock is test-only")
        if not frozen_now or not frozen_date:
            raise RuntimeError("Attention frozen clock requires both frozen now_ms and frozen_date")
        date.fromisoformat(frozen_date)
        return int(frozen_now), frozen_date
    return now_ms, None


def attention_today_date(*, now_ms: int, frozen_date: str | None = None) -> str:
    if frozen_date:
        date.fromisoformat(frozen_date)
        return frozen_date
    if os.environ.get("APP_ENV", "").strip().lower() == "test":
        raise RuntimeError("Attention tests must pass frozen_date explicitly")
    return date.fromtimestamp(now_ms / 1000).isoformat()


def require_attention_e2e_database(database_url: str) -> None:
    if os.environ.get("APP_ENV", "").strip().lower() != "test":
        raise RuntimeError("Attention E2E seeding requires APP_ENV=test")
    if os.environ.get("AUTH_REQUIRED", "").strip().lower() != "false":
        raise RuntimeError("Attention E2E seeding requires AUTH_REQUIRED=false")
    if os.environ.get("GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE", "").strip() != "1":
        raise RuntimeError(
            "Attention E2E seeding requires GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE=1"
        )
    conninfo = conninfo_to_dict(database_url)
    parsed = urlsplit(database_url)
    effective_host = (conninfo.get("host") or parsed.hostname or "").strip()
    effective_hostaddr = (conninfo.get("hostaddr") or "").strip()
    effective_db_name = (conninfo.get("dbname") or "").strip().lower()
    if effective_host.startswith("/"):
        host_allowed = effective_host in _ALLOWED_E2E_SOCKET_DIRS
    else:
        host_allowed = effective_host in _ALLOWED_E2E_HOSTS
    if not host_allowed:
        raise RuntimeError("Attention E2E database URL must use a local disposable database")
    if effective_hostaddr and effective_hostaddr not in _ALLOWED_E2E_HOSTADDRS:
        raise RuntimeError("Attention E2E database URL must use a local disposable database")
    db_name = effective_db_name or parsed.path.rsplit("/", 1)[-1].lower()
    if db_name != "gardenops_attention_e2e_test" and not db_name.startswith(
        "gardenops_attention_e2e_test_"
    ):
        raise RuntimeError(
            "Attention E2E database URL must point at a disposable e2e test database"
        )
