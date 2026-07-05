# Attention Model Map Today Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Attention domain and compact Today panel on the Map page so a user can open GardenOps, keep the Map front and center, and see a short, explainable set of garden items that need attention or need no action.

**Architecture:** Add a provider-based Attention package beside the existing task, weather, issue, calendar, and notification systems, then prove it through a thin vertical slice before broad provider expansion. The first executable slice is task-only: storage, core types, task provider, `/api/attention/today`, compact Map panel, and full-stack Playwright. Existing `notification_events` stay readable and unchanged by Attention in phase 1; generated watering outcomes are written by task/weather automation, then adapted by Attention.

**Tech Stack:** FastAPI, Pydantic strict request models, PostgreSQL SQL migrations, existing GardenOps DB wrapper, vanilla TypeScript, Vite, Playwright Core, pytest with the existing Postgres test database, Node static checks.

**Source Spec:** `docs/superpowers/specs/2026-07-04-attention-model-map-today-design.md`

---

## Scope

This plan implements the first production-safe Attention slice:

- New `/api/attention/*` backend API.
- New `attention` feature gate at the `enthusiast` tier, with `/api/attention` routed through the gate.
- New Attention-owned tables:
  - `user_attention_preferences`
  - `user_attention_item_state`
  - `attention_outcomes`
- First vertical slice: task provider, task-only `/api/attention/today`, compact Today panel, and full-stack Playwright.
- Second slice: weather-aware rain/watering no-action-needed outcomes written by task/weather automation and read by Attention.
- Third slice: issue, calendar, and legacy notification/status providers.
- Attention settings dialog with Calm, Balanced, Detailed, Custom, category/channel
  matrix controls, quiet-hour controls, and watering/weather preference metadata.
- Full backend tests plus Playwright coverage for both the task-only slice and the Morning Garden Check journey.

This plan intentionally does not migrate email digest or the existing notification inbox to Attention lifecycle ownership. The final customization slice lets notification inbox and digest delivery consult Attention preferences for eligibility, but `notification_events` remain the durable inbox/log records and Attention must not clear, supersede, rewrite, or delete those rows during read/filter paths.

## Resolved Implementation Choices

- Desktop placement: render `#attention-today-panel` as a compact right-side aside inside `.map-layout`, after `.map-stage` and before `#shade-panel`. It should not cover the map controls or block pan/zoom outside its own bounds.
- Mobile placement: render `#attention-today-mobile-handle` as a fixed bottom handle and `#attention-today-mobile-sheet` as a bottom sheet capped at 60vh. No drag gestures in this slice.
- Settings: add an Attention settings dialog opened from the Today panel. Keep the existing notification preference form intact.
- Accessibility checker: do not add an axe dependency in this slice. Playwright must assert role/name, keyboard, focus restore, reduced-motion behavior, and touch target contracts directly.
- Degraded provider copy: use "Some sources are temporarily unavailable." in the UI and expose provider keys only in a small diagnostic detail row.
- Rollback: disabling the `attention` feature gate hides the panel and rejects `/api/attention/*` for tiers without the feature. Existing notifications keep working.
- Modular package boundary: implement Attention as `gardenops/services/attention/`, not as one large module. Future feature work should add a provider file or preference rule, not edit the Map panel.
- Date basis: phase 1 uses the existing GardenOps ISO `due_on` date semantics. Do not introduce a new user/garden timezone model in this plan; every backend and Playwright Attention test uses explicit `GARDENOPS_ATTENTION_FROZEN_NOW_MS` and `GARDENOPS_ATTENTION_FROZEN_DATE`.
- E2E database safety: seed scripts must refuse to run unless the caller explicitly sets `APP_ENV=test`, `AUTH_REQUIRED=false`, `GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE=1`, and `DATABASE_URL` points to a local database named exactly `gardenops_attention_e2e_test` or prefixed `gardenops_attention_e2e_test_`.
- Frontend rendering: use DOM construction helpers or an approved sanitized-template path. Do not add broad `innerHTML` rendering in the Today panel.
- Read/composition boundary: `GET /api/attention/today` may read, normalize, rank, apply user state, and record non-domain user state through explicit mutation endpoints only. It must not create generated tasks, clear notifications, or invent automation outcomes during a read.

## File Structure

- Create `migrations/0019_attention_model.sql`
  - Adds Attention-owned persistence tables and indexes.
- Modify `gardenops/schema_signature.py`
  - Adds new tables, required columns, indexes, and constraints.
- Modify `gardenops/feature_gates.py`
  - Adds `attention` to the feature registry and gates `/api/attention`.
- Create `gardenops/services/attention/__init__.py`
  - Re-exports the stable public API used by routers and tests.
- Create `gardenops/services/attention/types.py`
  - Defines strict lifecycle vocabulary, item contracts, action contracts, provider protocol, and test clock helpers.
- Create `gardenops/services/attention/ranking.py`
  - Owns severity normalization, section ordering, grouping, and bounded output.
- Create `gardenops/services/attention/preferences.py`
  - Owns presets, legacy notification preference adaptation, guardrails, and channel conflict resolution.
- Create `gardenops/services/attention/outcomes.py`
  - Owns persisted automation outcome read/upsert helpers, retention, restore metadata, and safe recovery validation used by domain automation.
- Create `gardenops/services/attention/service.py`
  - Orchestrates providers, user state, preferences, degraded-provider handling, and API serialization.
- Create `gardenops/services/attention/providers/tasks.py`
  - Owns task attention mapping and the initial vertical slice provider.
- Create `gardenops/services/attention/providers/weather.py`
  - Owns weather alerts and adaptation of persisted rain/watering no-action-needed outcomes.
- Create `gardenops/services/attention/providers/issues.py`
  - Owns issue follow-ups and recently resolved issue outcomes.
- Create `gardenops/services/attention/providers/calendar.py`
  - Owns manual/generated calendar items that are not duplicates.
- Create `gardenops/services/attention/providers/notifications.py`
  - Owns read-only legacy notification/status adaptation.
- Create `gardenops/routers/attention.py`
  - Owns `/api/attention/today`, preferences, read, dismiss, snooze, and restore endpoints.
- Modify `gardenops/main.py`
  - Includes the Attention router.
- Create `tests/test_attention_service_unit.py`
  - Pure unit tests for normalization, ranking, grouping, preference resolution, guardrails, and generated-watering predicates.
- Create `tests/test_attention_api.py`
  - Postgres-backed API and provider tests using `BaseApiTest`.
- Modify `tests/test_integrity.py`
  - Extends schema signature coverage for the new Attention tables.
- Modify `frontend/src/core/models.ts`
  - Adds Attention response, item, action, preference, and state types.
- Modify `frontend/src/services/api.ts`
  - Adds typed Attention API helpers.
- Create `frontend/src/components/attentionTodayPanel.ts`
  - Renders desktop panel, mobile handle/sheet, sections, actions, settings dialog, keyboard behavior, and refresh hooks.
- Modify `frontend/src/components/layout.ts`
  - Adds Today panel anchors in the Map markup.
- Modify `frontend/src/app.ts`
  - Initializes Attention, loads the feed on Map activation and garden changes, applies map context, and routes item actions to existing workflows.
- Modify `frontend/src/core/i18n.ts`
  - Adds English and Norwegian UI strings.
- Modify `frontend/src/style.css`
  - Adds responsive panel, sheet, focus, touch target, grouping, and reduced-motion styles.
- Create `scripts/check_attention_today_contract.cjs`
  - Static contract check for API helpers, panel hooks, anchors, feature gate, and test ids.
- Create `scripts/check_attention_today_e2e.cjs`
  - Playwright journey against real frontend rendering and the real FastAPI backend.
- Create `scripts/seed_attention_today_e2e.py`
  - Deterministic Postgres seed for the full-stack Playwright journey.
- Create `scripts/run_attention_today_e2e.sh`
  - Managed local E2E runner that seeds, starts FastAPI and Vite, waits for readiness, runs Playwright, and cleans up child processes.
- Create `scripts/check_attention_e2e_db_safety.py`
  - AST and runtime guard check proving E2E seed scripts refuse unsafe database URLs and guard before any destructive write.
- Modify `gardenops/services/task_generator.py`
  - Records rain-covered generated watering outcomes when existing generation logic suppresses task creation.
- Modify `gardenops/services/notification_service.py`
  - Reuses existing stale-notification maintenance for generated rain-suppressed task notifications; Attention does not mutate notification rows.
- Modify `frontend/package.json`
  - Adds `check:attention-today` and includes it in the build chain.
- Modify `README.md` only if documentation-upkeep says the user-facing feature needs a public feature bullet in the implementation PR.

## Data Model

Migration `migrations/0019_attention_model.sql`:

```sql
CREATE TABLE IF NOT EXISTS public.user_attention_preferences (
    id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    user_id bigint NOT NULL,
    preset text DEFAULT 'balanced'::text NOT NULL,
    rules_json text DEFAULT '{}'::text NOT NULL,
    quiet_hours_json text DEFAULT '{}'::text NOT NULL,
    show_no_action_history bigint DEFAULT 1 NOT NULL,
    created_at_ms bigint NOT NULL,
    updated_at_ms bigint NOT NULL,
    CONSTRAINT ux_user_attention_preferences_user UNIQUE (user_id),
    CONSTRAINT fk_user_attention_preferences_user
        FOREIGN KEY (user_id)
        REFERENCES public.auth_users(id)
        ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_user_attention_preferences_no_action_bool
        CHECK (show_no_action_history IN (0, 1))
);

CREATE TABLE IF NOT EXISTS public.user_attention_item_state (
    id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    user_id bigint NOT NULL,
    garden_id bigint NOT NULL,
    item_id text NOT NULL,
    user_state text NOT NULL,
    snoozed_until_ms bigint,
    reason text DEFAULT ''::text NOT NULL,
    metadata_json text DEFAULT '{}'::text NOT NULL,
    created_at_ms bigint NOT NULL,
    updated_at_ms bigint NOT NULL,
    CONSTRAINT ux_user_attention_item_state_user_garden_item UNIQUE (user_id, garden_id, item_id),
    CONSTRAINT fk_user_attention_item_state_user
        FOREIGN KEY (user_id)
        REFERENCES public.auth_users(id)
        ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT fk_user_attention_item_state_garden
        FOREIGN KEY (garden_id)
        REFERENCES public.gardens(id)
        ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS idx_user_attention_item_state_garden_user
    ON public.user_attention_item_state USING btree (garden_id, user_id, user_state, snoozed_until_ms);

CREATE TABLE IF NOT EXISTS public.attention_outcomes (
    id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    public_id text NOT NULL UNIQUE,
    garden_id bigint NOT NULL,
    provider text NOT NULL,
    outcome_type text NOT NULL,
    source_type text NOT NULL,
    source_id text DEFAULT ''::text NOT NULL,
    source_public_id text DEFAULT ''::text NOT NULL,
    title text NOT NULL,
    explanation text NOT NULL,
    reason text DEFAULT ''::text NOT NULL,
    target_type text DEFAULT ''::text NOT NULL,
    target_id text DEFAULT ''::text NOT NULL,
    plant_ids_json text DEFAULT '[]'::text NOT NULL,
    plot_ids_json text DEFAULT '[]'::text NOT NULL,
    recovery_action_json text DEFAULT '{}'::text NOT NULL,
    metadata_json text DEFAULT '{}'::text NOT NULL,
    occurred_at_ms bigint NOT NULL,
    expires_at_ms bigint NOT NULL,
    created_at_ms bigint NOT NULL,
    updated_at_ms bigint NOT NULL,
    CONSTRAINT fk_attention_outcomes_garden
        FOREIGN KEY (garden_id)
        REFERENCES public.gardens(id)
        ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS idx_attention_outcomes_garden_expires
    ON public.attention_outcomes USING btree (garden_id, expires_at_ms DESC, occurred_at_ms DESC);

CREATE INDEX IF NOT EXISTS idx_attention_outcomes_source
    ON public.attention_outcomes USING btree (garden_id, provider, source_type, source_id);

CREATE UNIQUE INDEX IF NOT EXISTS ux_attention_outcomes_source_kind
    ON public.attention_outcomes USING btree (
        garden_id,
        provider,
        outcome_type,
        source_type,
        source_public_id,
        target_type,
        target_id
    );
```

Stable item IDs:

- Task: `attn:task:{garden_tasks.public_id}`
- Weather alert: `attn:weather:{weather_alerts.id}`
- Issue: `attn:issue:{garden_issues.public_id}`
- Calendar: `attn:calendar:{garden_calendar_events.public_id}`
- Legacy notification/status: `attn:notification:{notification_events.public_id}`
- Persisted outcome: `attn:outcome:{attention_outcomes.public_id}`
- Group: `attn:group:{provider}:{group_key}:{hash_of_child_ids}`

## Backend Contract

`gardenops/services/attention/types.py` owns these literals and dataclasses:

```python
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
```

`gardenops/routers/attention.py` exposes:

```text
GET /api/attention/today
GET /api/attention/preferences
PUT /api/attention/preferences
POST /api/attention/items/{item_id}/read
POST /api/attention/items/{item_id}/dismiss
POST /api/attention/items/{item_id}/snooze
POST /api/attention/items/{item_id}/restore
```

Response sections are always bounded:

- Active sections: top five items before "view all".
- No action needed section: top five outcomes by `occurred_at_ms DESC`.
- Provider failures: no raw exception strings, only provider key and `"degraded"`.

## Task 0: Execution Safety, Frozen Clock, And Slice Gates

**Files:**
- Create: `gardenops/services/attention/__init__.py`
- Create: `gardenops/services/attention/types.py`
- Create: `scripts/check_attention_e2e_db_safety.py`
- Create: `tests/test_attention_service_unit.py`

- [ ] **Step 1: Write failing unit tests for the frozen clock and E2E DB guard**

Create `tests/test_attention_service_unit.py` with:

```python
import os

import pytest

from gardenops.services.attention import (
    attention_request_clock,
    attention_today_date,
    require_attention_e2e_database,
)


def test_attention_today_date_uses_explicit_frozen_date() -> None:
    assert attention_today_date(now_ms=1783180800000, frozen_date="2026-07-05") == "2026-07-05"


def test_attention_today_date_rejects_missing_clock_in_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    with pytest.raises(RuntimeError, match="frozen_date"):
        attention_today_date(now_ms=1783180800000, frozen_date=None)


def test_attention_request_clock_reads_explicit_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("GARDENOPS_ATTENTION_FROZEN_NOW_MS", "1783180800000")
    monkeypatch.setenv("GARDENOPS_ATTENTION_FROZEN_DATE", "2026-07-05")

    now_ms, frozen_date = attention_request_clock(now_ms=99)

    assert now_ms == 1783180800000
    assert frozen_date == "2026-07-05"


def test_attention_request_clock_rejects_test_env_outside_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("GARDENOPS_ATTENTION_FROZEN_DATE", "2026-07-05")

    with pytest.raises(RuntimeError, match="test-only"):
        attention_request_clock(now_ms=99)


def test_attention_e2e_database_guard_rejects_non_test_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE", "1")
    with pytest.raises(RuntimeError, match="disposable"):
        require_attention_e2e_database("postgresql://localhost/gardenops")


def test_attention_e2e_database_guard_rejects_missing_allow_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.delenv("GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE", raising=False)

    with pytest.raises(RuntimeError, match="ALLOW_TRUNCATE"):
        require_attention_e2e_database("postgresql://localhost/gardenops_attention_e2e_test")


def test_attention_e2e_database_guard_accepts_named_test_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE", "1")
    require_attention_e2e_database("postgresql://localhost/gardenops_attention_e2e_test")
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
uv run pytest tests/test_attention_service_unit.py -q
```

Expected: FAIL because the Attention package and guard helpers do not exist.

- [ ] **Step 3: Create the Attention package entrypoint and clock helpers**

Create `gardenops/services/attention/types.py`:

```python
from __future__ import annotations

import os
from datetime import date
from urllib.parse import urlsplit


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
        raise RuntimeError("Attention E2E seeding requires GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE=1")
    parsed = urlsplit(database_url)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("Attention E2E database URL must use a local disposable database")
    db_name = parsed.path.rsplit("/", 1)[-1].lower()
    if db_name != "gardenops_attention_e2e_test" and not db_name.startswith("gardenops_attention_e2e_test_"):
        raise RuntimeError("Attention E2E database URL must point at a disposable e2e test database")
```

Create `gardenops/services/attention/__init__.py`:

```python
from gardenops.services.attention.types import (
    attention_request_clock,
    attention_today_date,
    require_attention_e2e_database,
)

__all__ = [
    "attention_request_clock",
    "attention_today_date",
    "require_attention_e2e_database",
]
```

- [ ] **Step 4: Add the safety script contract**

Create `scripts/check_attention_e2e_db_safety.py`:

```python
#!/usr/bin/env python3

from __future__ import annotations

import ast
import os
from pathlib import Path

from gardenops.services.attention import require_attention_e2e_database


class SeedSafetyVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.setdefault_lines: list[int] = []
        self.main_guard_line: int | None = None
        self.main_db_touch_lines: list[int] = []

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "setdefault"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "environ"
        ):
            self.setdefault_lines.append(node.lineno)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name != "main":
            self.generic_visit(node)
            return
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            if isinstance(func, ast.Name) and func.id == "require_attention_e2e_database":
                self.main_guard_line = inner.lineno
            if isinstance(func, ast.Name) and func.id in {
                "truncate_public_tables",
                "create_user",
            }:
                self.main_db_touch_lines.append(inner.lineno)
            if isinstance(func, ast.Attribute) and func.attr in {
                "run_migrations",
                "get_db",
                "ensure_default_garden",
                "execute",
                "commit",
            }:
                self.main_db_touch_lines.append(inner.lineno)
        self.generic_visit(node)


def main() -> None:
    seed = Path("scripts/seed_attention_today_e2e.py")
    if not seed.exists():
        raise SystemExit("scripts/seed_attention_today_e2e.py is missing")
    source = seed.read_text(encoding="utf-8")
    tree = ast.parse(source)
    visitor = SeedSafetyVisitor()
    visitor.visit(tree)
    if visitor.setdefault_lines:
        raise SystemExit(f"seed script must not set default environment values: {visitor.setdefault_lines}")
    if visitor.main_guard_line is None:
        raise SystemExit("seed script must call require_attention_e2e_database")
    before_guard = [line for line in visitor.main_db_touch_lines if line < visitor.main_guard_line]
    if before_guard:
        raise SystemExit(f"database work appears before E2E guard: {before_guard}")

    os.environ["APP_ENV"] = "test"
    os.environ["AUTH_REQUIRED"] = "false"
    os.environ.pop("GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE", None)
    try:
        require_attention_e2e_database("postgresql://localhost/gardenops_attention_e2e_test")
    except RuntimeError as exc:
        if "ALLOW_TRUNCATE" not in str(exc):
            raise
    else:
        raise SystemExit("database guard accepted missing allow flag")

    os.environ["GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE"] = "1"
    try:
        require_attention_e2e_database("postgresql://db.example.com/gardenops_attention_e2e_test")
    except RuntimeError as exc:
        if "local" not in str(exc):
            raise
    else:
        raise SystemExit("database guard accepted non-local database host")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_attention_service_unit.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gardenops/services/attention/__init__.py gardenops/services/attention/types.py scripts/check_attention_e2e_db_safety.py tests/test_attention_service_unit.py
git commit -m "test: add attention execution safety guards"
```

## Task 1: Feature Gate, Migration, And Schema Integrity

**Files:**
- Create: `migrations/0019_attention_model.sql`
- Modify: `gardenops/schema_signature.py`
- Modify: `gardenops/feature_gates.py`
- Create: `tests/test_attention_api.py`
- Modify: `tests/test_integrity.py`

- [ ] **Step 1: Write failing storage and feature-gate tests**

Add this first test class to `tests/test_attention_api.py`:

```python
from unittest.mock import patch

import gardenops.db as db
from tests.base import BaseApiTest


class TestAttentionStorageAndGate(BaseApiTest):
    def test_attention_tables_exist_after_migrations(self) -> None:
        conn = db.get_db()
        try:
            tables = {
                row["tablename"]
                for row in conn.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'",
                ).fetchall()
            }
        finally:
            db.return_db(conn)

        self.assertIn("user_attention_preferences", tables)
        self.assertIn("user_attention_item_state", tables)
        self.assertIn("attention_outcomes", tables)

    def test_attention_route_is_tier_gated(self) -> None:
        from gardenops.feature_gates import feature_allowed, feature_for_route

        self.assertEqual(feature_for_route("/api/attention/today"), "attention")
        self.assertFalse(feature_allowed("home", "attention"))
        self.assertTrue(feature_allowed("enthusiast", "attention"))

    def test_attention_prefix_rejects_home_tier_requests_before_router_exists(self) -> None:
        conn = db.get_db()
        try:
            conn.execute(
                "UPDATE auth_users SET subscription_tier = 'home' WHERE username = 'test_admin'",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        self.assertEqual(self.client.get("/api/attention/today").status_code, 403)
        self.assertEqual(self.client.put("/api/attention/preferences", json={}).status_code, 403)
        self.assertEqual(self.client.post("/api/attention/items/attn:task:demo/read", json={}).status_code, 403)
```

- [ ] **Step 2: Run the focused tests to verify RED**

Run:

```bash
uv run pytest tests/test_attention_api.py::TestAttentionStorageAndGate -q
```

Expected: FAIL because the tables and `attention` feature key do not exist.

- [ ] **Step 3: Add the migration**

Create `migrations/0019_attention_model.sql` using the SQL in the Data Model section.

- [ ] **Step 4: Update schema signature**

In `gardenops/schema_signature.py`, add these tables to `REQUIRED_TABLES`:

```python
"user_attention_preferences",
"user_attention_item_state",
"attention_outcomes",
```

Add these `REQUIRED_COLUMNS` entries:

```python
"user_attention_preferences": (
    "id",
    "user_id",
    "preset",
    "rules_json",
    "quiet_hours_json",
    "show_no_action_history",
    "created_at_ms",
    "updated_at_ms",
),
"user_attention_item_state": (
    "id",
    "user_id",
    "garden_id",
    "item_id",
    "user_state",
    "snoozed_until_ms",
    "reason",
    "metadata_json",
    "created_at_ms",
    "updated_at_ms",
),
"attention_outcomes": (
    "id",
    "public_id",
    "garden_id",
    "provider",
    "outcome_type",
    "source_type",
    "source_id",
    "source_public_id",
    "title",
    "explanation",
    "reason",
    "target_type",
    "target_id",
    "plant_ids_json",
    "plot_ids_json",
    "recovery_action_json",
    "metadata_json",
    "occurred_at_ms",
    "expires_at_ms",
    "created_at_ms",
    "updated_at_ms",
),
```

Add these index names to `REQUIRED_INDEXES`:

```python
"idx_user_attention_item_state_garden_user",
"idx_attention_outcomes_garden_expires",
"idx_attention_outcomes_source",
"ux_attention_outcomes_source_kind",
```

Add these constraint names to `REQUIRED_CONSTRAINTS`:

```python
"ux_user_attention_preferences_user",
"fk_user_attention_preferences_user",
"ck_user_attention_preferences_no_action_bool",
"ux_user_attention_item_state_user_garden_item",
"fk_user_attention_item_state_user",
"fk_user_attention_item_state_garden",
"attention_outcomes_public_id_key",
"fk_attention_outcomes_garden",
```

- [ ] **Step 5: Add the feature gate**

In `gardenops/feature_gates.py`, add:

```python
"attention": "enthusiast",
```

to `_FEATURE_TIERS`, and add this route gate before the professional routes:

```python
("/api/attention", "attention"),
```

- [ ] **Step 6: Run tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_attention_api.py::TestAttentionStorageAndGate tests/test_integrity.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add migrations/0019_attention_model.sql gardenops/schema_signature.py gardenops/feature_gates.py tests/test_attention_api.py tests/test_integrity.py
git commit -m "feat: add attention model storage"
```

## Task 2: Attention Core Types, Ranking, Grouping, And Preferences

**Files:**
- Modify: `gardenops/services/attention/__init__.py`
- Modify: `gardenops/services/attention/types.py`
- Create: `gardenops/services/attention/ranking.py`
- Create: `gardenops/services/attention/preferences.py`
- Modify: `tests/test_attention_service_unit.py`

- [ ] **Step 1: Write failing unit tests for vocabulary and ordering**

Append these tests to `tests/test_attention_service_unit.py`:

```python
from gardenops.services.attention import (
    AttentionAction,
    AttentionItem,
    AttentionPreferenceSet,
    apply_preferences,
    group_attention_items,
    rank_attention_items,
    resolve_attention_preferences,
)


def make_item(**overrides):
    base = {
        "id": "attn:task:task_1",
        "provider": "task",
        "type": "task_due",
        "category": "needs_action",
        "severity": "normal",
        "title": "Water basil",
        "body": "Water basil today.",
        "reason": "Due today",
        "target_type": "task",
        "target_id": "task_1",
        "garden_id": 1,
        "audience_user_id": 2,
        "due_on": "2026-07-05",
        "updated_at_ms": 1783180800000,
        "primary_action": AttentionAction(
            kind="open_task",
            label="Open task",
            target_type="task",
            target_id="task_1",
        ),
    }
    base.update(overrides)
    return AttentionItem(**base)


def test_rank_attention_items_prioritizes_high_warnings_before_due_tasks():
    items = [
        make_item(id="attn:task:due", severity="normal", type="task_due"),
        make_item(
            id="attn:weather:frost",
            provider="weather",
            type="frost_warning",
            category="warning",
            severity="high",
            title="Protect basil from frost",
        ),
    ]

    ranked = rank_attention_items(items)

    assert [item.id for item in ranked] == ["attn:weather:frost", "attn:task:due"]


def test_group_attention_items_does_not_hide_high_severity_in_low_group():
    low = make_item(id="attn:task:water_low", group_key="water:plot:A", severity="low")
    high = make_item(id="attn:task:water_high", group_key="water:plot:A", severity="high")

    grouped = group_attention_items([low, high])

    assert [item.id for item in grouped] == ["attn:task:water_high", "attn:task:water_low"]


def test_balanced_preferences_keep_panel_visible_when_inbox_is_disabled():
    prefs = resolve_attention_preferences(
        user_id=2,
        legacy_preferences={
            "in_app_enabled": False,
            "email_enabled": False,
            "notification_rules": {
                "task_due": {
                    "in_app_enabled": False,
                    "email_enabled": False,
                    "min_severity": "low",
                },
            },
        },
        saved_attention_preferences=None,
    )
    item = make_item(type="task_due", delivery_eligibility=("panel_only", "inbox"))

    visible = apply_preferences([item], prefs, surface="panel")

    assert [i.id for i in visible] == ["attn:task:task_1"]
    assert prefs.preset == "custom"


def test_guardrail_keeps_critical_system_visible_on_non_email_surface():
    prefs = AttentionPreferenceSet(
        user_id=2,
        preset="custom",
        rules={
            "system": {
                "panel": False,
                "inbox": False,
                "digest": False,
                "min_severity": "critical",
            }
        },
        quiet_hours={},
        show_no_action_history=True,
    )
    item = make_item(
        id="attn:notification:system",
        provider="notification_status",
        type="system",
        category="system",
        severity="critical",
    )

    visible = apply_preferences([item], prefs, surface="panel")

    assert visible[0].id == "attn:notification:system"
    assert visible[0].user_state == "unread"
```

- [ ] **Step 2: Run unit tests to verify RED**

Run:

```bash
uv run pytest tests/test_attention_service_unit.py -q
```

Expected: FAIL because `AttentionAction`, `AttentionItem`, ranking, grouping, and preference helpers do not exist.

- [ ] **Step 3: Implement core types and pure functions**

Extend `gardenops/services/attention/types.py` with the dataclasses from the Backend Contract section and these first helpers:

```python
RAIN_COVERS_WATERING_MM = 10.0
NO_ACTION_RETENTION_DAYS = 30
SEVERITY_RANK = {"low": 0, "normal": 1, "high": 2, "critical": 3}


def stable_group_id(provider: str, group_key: str, child_ids: list[str]) -> str:
    digest = hashlib.sha256("|".join(sorted(child_ids)).encode("utf-8")).hexdigest()[:16]
    return f"attn:group:{provider}:{group_key}:{digest}"


def is_generated_watering_task(task_type: str, rule_source: str | None) -> bool:
    value = (rule_source or "").strip()
    return task_type == "water" and (value.startswith("water:") or value.startswith("auto:dry_water:"))


def normalize_severity(value: str | None) -> AttentionSeverity:
    lowered = (value or "normal").strip().lower()
    return lowered if lowered in SEVERITY_RANK else "normal"
```

Create `gardenops/services/attention/ranking.py` with `rank_attention_items()` and `group_attention_items()`.

Create `gardenops/services/attention/preferences.py` with `AttentionPreferenceSet`, `resolve_attention_preferences()`, and `apply_preferences()` to satisfy the tests and the spec conflict order:

1. Domain state.
2. User state.
3. Guardrails.
4. Quiet hours for digest or interruptive delivery only.
5. Channel eligibility.

Update `gardenops/services/attention/__init__.py` to re-export:

```python
from gardenops.services.attention.preferences import (
    AttentionPreferenceSet,
    apply_preferences,
    resolve_attention_preferences,
)
from gardenops.services.attention.ranking import group_attention_items, rank_attention_items
from gardenops.services.attention.types import AttentionAction, AttentionItem
```

- [ ] **Step 4: Run unit tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_attention_service_unit.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gardenops/services/attention/__init__.py gardenops/services/attention/types.py gardenops/services/attention/ranking.py gardenops/services/attention/preferences.py tests/test_attention_service_unit.py
git commit -m "feat: add attention service core"
```

## Task 3: Task Provider Vertical Slice

**Files:**
- Create: `gardenops/services/attention/providers/__init__.py`
- Create: `gardenops/services/attention/providers/tasks.py`
- Modify: `gardenops/services/attention/__init__.py`
- Modify: `tests/test_attention_api.py`

- [ ] **Step 1: Write failing task-provider tests**

Add `TestAttentionTaskProvider` to `tests/test_attention_api.py`:

```python
class TestAttentionTaskProvider(BaseApiTest):
    def _garden_and_user(self) -> tuple[int, int]:
        conn = db.get_db()
        try:
            garden_id = int(conn.execute("SELECT id FROM gardens WHERE slug = 'default'").fetchone()["id"])
            user_id = int(conn.execute("SELECT id FROM auth_users WHERE username = 'test_admin'").fetchone()["id"])
            return garden_id, user_id
        finally:
            db.return_db(conn)

    def test_task_provider_maps_due_overdue_snoozed_and_plot_context(self) -> None:
        from gardenops.services.attention import TaskAttentionProvider

        garden_id, user_id = self._garden_and_user()
        conn = db.get_db()
        try:
            conn.execute(
                "INSERT INTO plots (plot_id, garden_id, zone_code, zone_name, plot_number, grid_row, grid_col, sub_zone, notes) "
                "VALUES ('A1', %s, 'A', 'Beds', 1, 1, 1, '', '')",
                (garden_id,),
            )
            conn.execute(
                """
                INSERT INTO garden_tasks
                (public_id, garden_id, task_type, title, description, status, severity, due_on,
                 snoozed_until, rule_source, metadata_json, created_at_ms, updated_at_ms)
                VALUES
                ('task_due', %s, 'water', 'Water basil', '', 'pending', 'normal', '2026-07-05', NULL, '', '{}', 1, 1),
                ('task_overdue', %s, 'prune', 'Prune roses', '', 'pending', 'high', '2026-07-04', NULL, '', '{}', 1, 1),
                ('task_snoozed_ready', %s, 'harvest', 'Harvest lettuce', '', 'snoozed', 'normal', '2026-07-05', '2026-07-05', '', '{}', 1, 1),
                ('task_snoozed_future', %s, 'harvest', 'Harvest cabbage', '', 'snoozed', 'normal', '2026-07-05', '2026-07-07', '', '{}', 1, 1),
                ('task_completed', %s, 'water', 'Water parsley', '', 'completed', 'normal', '2026-07-05', NULL, '', '{}', 1, 1)
                """,
                (garden_id, garden_id, garden_id, garden_id, garden_id),
            )
            due_id = int(conn.execute("SELECT id FROM garden_tasks WHERE public_id = 'task_due'").fetchone()["id"])
            conn.execute("INSERT INTO garden_task_plots (task_id, plot_id) VALUES (%s, 'A1')", (due_id,))
            conn.commit()
            items = TaskAttentionProvider(frozen_date="2026-07-05").collect(
                conn,
                garden_id=garden_id,
                user_id=user_id,
                now_ms=1783180800000,
            )
        finally:
            db.return_db(conn)

        by_id = {item.id: item for item in items}
        assert by_id["attn:task:task_due"].type == "task_due"
        assert by_id["attn:task:task_due"].plot_ids == ("A1",)
        assert by_id["attn:task:task_overdue"].type == "task_overdue"
        assert by_id["attn:task:task_snoozed_ready"].type == "task_snoozed_active"
        assert "attn:task:task_snoozed_future" not in by_id
        assert by_id["attn:task:task_completed"].category == "no_action_needed"
        assert by_id["attn:task:task_completed"].reason == "Completed"
```

- [ ] **Step 2: Run provider tests to verify RED**

Run:

```bash
uv run pytest tests/test_attention_api.py::TestAttentionTaskProvider -q
```

Expected: FAIL because `TaskAttentionProvider` does not exist.

- [ ] **Step 3: Implement only the task provider**

Create `gardenops/services/attention/providers/__init__.py`:

```python
from gardenops.services.attention.providers.tasks import TaskAttentionProvider

__all__ = ["TaskAttentionProvider"]
```

Create `gardenops/services/attention/providers/tasks.py`:

```python
from __future__ import annotations

from gardenops.db import DbConn
from gardenops.services.attention.types import AttentionAction, AttentionItem, attention_today_date


class TaskAttentionProvider:
    key = "task"

    def __init__(self, *, frozen_date: str | None = None) -> None:
        self.frozen_date = frozen_date

    def collect(self, conn: DbConn, *, garden_id: int, user_id: int, now_ms: int) -> list[AttentionItem]:
        today = attention_today_date(now_ms=now_ms, frozen_date=self.frozen_date)
        rows = conn.execute(
            """
            SELECT t.*
            FROM garden_tasks t
            WHERE t.garden_id = %s
              AND t.status IN ('pending', 'snoozed', 'completed', 'skipped')
              AND (
                (t.status = 'pending' AND t.due_on <= %s)
                OR (t.status = 'snoozed' AND t.snoozed_until IS NOT NULL AND t.snoozed_until <= %s)
                OR (t.status IN ('completed', 'skipped') AND t.updated_at_ms >= %s)
              )
            """,
            (garden_id, today, today, now_ms - 86_400_000),
        ).fetchall()
        return [self._item_from_row(conn, row, garden_id=garden_id, user_id=user_id, today=today) for row in rows]

    def _item_from_row(self, conn: DbConn, row: dict, *, garden_id: int, user_id: int, today: str) -> AttentionItem:
        task_id = int(row["id"])
        public_id = str(row["public_id"])
        plot_ids = tuple(
            str(plot["plot_id"])
            for plot in conn.execute(
                "SELECT plot_id FROM garden_task_plots WHERE task_id = %s ORDER BY plot_id",
                (task_id,),
            ).fetchall()
        )
        task_status = str(row["status"])
        due_on = str(row["snoozed_until"] or row["due_on"])
        item_type = "task_due"
        if task_status == "snoozed":
            item_type = "task_snoozed_active"
        elif due_on < today:
            item_type = "task_overdue"
        elif task_status in {"completed", "skipped"}:
            item_type = f"task_{task_status}"
        reason = "Overdue" if item_type == "task_overdue" else "Due today"
        if task_status == "snoozed":
            reason = "Snooze expired"
        if task_status == "completed":
            reason = "Completed"
        if task_status == "skipped":
            reason = "Skipped"
        primary_action = None
        if task_status in {"pending", "snoozed"}:
            primary_action = AttentionAction(kind="open_task", label="Open task", target_type="task", target_id=public_id)
        return AttentionItem(
            id=f"attn:task:{public_id}",
            provider="task",
            type=item_type,
            category="needs_action" if task_status in {"pending", "snoozed"} else "no_action_needed",
            severity=str(row["severity"] or "normal"),
            title=str(row["title"]),
            body=str(row["description"] or ""),
            reason=reason,
            target_type="task",
            target_id=public_id,
            garden_id=garden_id,
            audience_user_id=user_id,
            plot_ids=plot_ids,
            due_on=due_on,
            domain_state="active" if task_status in {"pending", "snoozed"} else task_status,
            primary_action=primary_action,
            source_label="Tasks",
            updated_at_ms=int(row["updated_at_ms"]),
        )
```

Update `gardenops/services/attention/__init__.py` to re-export `TaskAttentionProvider`.

- [ ] **Step 4: Run provider tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_attention_api.py::TestAttentionTaskProvider -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gardenops/services/attention/__init__.py gardenops/services/attention/providers/__init__.py gardenops/services/attention/providers/tasks.py tests/test_attention_api.py
git commit -m "feat: add task attention provider"
```

## Task 4: Attention Service, User State, Outcomes, And Today API

**Files:**
- Create: `gardenops/services/attention/service.py`
- Modify: `gardenops/services/attention/__init__.py`
- Create: `gardenops/routers/attention.py`
- Modify: `gardenops/main.py`
- Modify: `tests/test_attention_api.py`

- [ ] **Step 1: Write failing API tests**

Add `TestAttentionTodayApi` to `tests/test_attention_api.py`:

```python
class TestAttentionTodayApi(BaseApiTest):
    def test_today_returns_bounded_sections_and_stable_ids(self) -> None:
        conn = db.get_db()
        try:
            garden_id = int(conn.execute("SELECT id FROM gardens WHERE slug = 'default'").fetchone()["id"])
            for idx in range(7):
                conn.execute(
                    """
                    INSERT INTO garden_tasks
                    (public_id, garden_id, task_type, title, description, status, severity, due_on,
                     rule_source, metadata_json, created_at_ms, updated_at_ms)
                    VALUES (%s, %s, 'water', %s, '', 'pending', 'normal', '2026-07-05', '', '{}', 1, 1)
                    """,
                    (f"task_due_{idx}", garden_id, f"Water plant {idx}"),
                )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            "os.environ",
            {
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783180800000",
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
            },
        ):
            r = self.client.get("/api/attention/today")

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["sections"][0]["key"], "needs_attention")
        self.assertLessEqual(len(body["sections"][0]["items"]), 5)
        self.assertTrue(body["sections"][0]["items"][0]["id"].startswith("attn:"))

    def test_today_uses_task_provider_only_in_first_slice(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "GARDENOPS_ATTENTION_FROZEN_NOW_MS": "1783180800000",
                "GARDENOPS_ATTENTION_FROZEN_DATE": "2026-07-05",
            },
        ):
            r = self.client.get("/api/attention/today")
        self.assertEqual(r.status_code, 200)
        providers = {
            item["provider"]
            for section in r.json()["sections"]
            for item in section["items"]
        }
        self.assertLessEqual(providers, {"task"})
```

The `force_degraded_provider` query parameter is added later when the second provider is introduced.

- [ ] **Step 2: Run API tests to verify RED**

Run:

```bash
uv run pytest tests/test_attention_api.py::TestAttentionTodayApi -q
```

Expected: FAIL because the router and service orchestration do not exist.

- [ ] **Step 3: Implement the service orchestration**

Add `AttentionService`:

```python
class AttentionService:
    def __init__(
        self,
        providers: list[AttentionProvider] | None = None,
        *,
        frozen_date: str | None = None,
    ) -> None:
        self.providers = providers or [
            TaskAttentionProvider(frozen_date=frozen_date),
        ]

    def today(
        self,
        conn: DbConn,
        *,
        garden_id: int,
        user_id: int,
        now_ms: int,
        force_degraded_provider: str | None = None,
    ) -> dict[str, Any]:
        preferences = load_attention_preferences(conn, user_id=user_id)
        user_states = load_user_attention_states(conn, garden_id=garden_id, user_id=user_id, now_ms=now_ms)
        items: list[AttentionItem] = []
        degraded: list[dict[str, str]] = []
        for provider_index, provider in enumerate(self.providers):
            savepoint = f"attention_provider_{provider_index}"
            conn.execute(f"SAVEPOINT {savepoint}")
            try:
                if force_degraded_provider == provider.key:
                    raise RuntimeError("forced test degradation")
                items.extend(provider.collect(conn, garden_id=garden_id, user_id=user_id, now_ms=now_ms))
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            except Exception:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                logger.exception("attention provider failed", extra={"provider": provider.key})
                degraded.append({"provider": provider.key, "status": "degraded"})
        items = apply_user_states(items, user_states, now_ms=now_ms)
        items = apply_preferences(items, preferences, surface="panel")
        items = group_attention_items(rank_attention_items(items))
        return serialize_today_response(
            garden_id=garden_id,
            generated_at_ms=now_ms,
            items=items,
            preferences=preferences,
            degraded_providers=degraded,
        )
```

Add helpers:

- `load_attention_preferences()`: load saved Attention preferences or derive from `user_notification_preferences`.
- `save_attention_preferences()`: validate preset/rules and upsert.
- `load_user_attention_states()`: ignore expired snoozes.
- `set_user_attention_state()`: upsert read/dismiss/snooze/preference_hidden.
- `serialize_today_response()`: build sections and counts.
- `restore_attention_outcome()`: validate outcome and return 409 until watering restore actions are implemented.

Create this in `gardenops/services/attention/service.py`, and re-export `AttentionService` from `gardenops/services/attention/__init__.py`.

- [ ] **Step 4: Implement the router**

Create `gardenops/routers/attention.py`:

```python
from __future__ import annotations

import os
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field

from gardenops.db import DB, current_timestamp_ms
from gardenops.models import StrictBaseModel
from gardenops.router_helpers import active_garden_id as _active_garden_id
from gardenops.router_helpers import auth_context as _auth_context
from gardenops.services.attention import (
    AttentionService,
    attention_request_clock,
    save_attention_preferences,
    set_user_attention_state,
)

router = APIRouter()


class AttentionPreferencesBody(StrictBaseModel):
    preset: Literal["calm", "balanced", "detailed", "custom"] = "balanced"
    rules: dict[str, dict[str, bool | str]] = Field(default_factory=dict)
    quiet_hours: dict[str, Any] = Field(default_factory=dict)
    show_no_action_history: bool = True


class AttentionSnoozeBody(StrictBaseModel):
    snoozed_until_ms: int = Field(ge=0)


@router.get("/attention/today")
def get_attention_today(
    request: Request,
    db: DB,
) -> dict[str, Any]:
    context = _auth_context(request)
    garden_id = _active_garden_id(context)
    now_ms, frozen_date = attention_request_clock(now_ms=current_timestamp_ms())
    return AttentionService(frozen_date=frozen_date).today(
        db,
        garden_id=garden_id,
        user_id=context.user_id,
        now_ms=now_ms,
    )
```

Add the remaining endpoints with the same auth/garden context pattern.

In `gardenops/main.py`, import and include:

```python
from gardenops.routers.attention import router as attention_router
app.include_router(attention_router, prefix="/api")
```

- [ ] **Step 5: Run API tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_attention_api.py::TestAttentionTodayApi -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gardenops/services/attention/__init__.py gardenops/services/attention/service.py gardenops/routers/attention.py gardenops/main.py tests/test_attention_api.py
git commit -m "feat: add attention today api"
```

## Task 5: Frontend Contract And Task-Only Today Panel

**Files:**
- Modify: `frontend/src/core/models.ts`
- Modify: `frontend/src/services/api.ts`
- Modify: `frontend/src/components/layout.ts`
- Create: `frontend/src/components/attentionTodayPanel.ts`
- Modify: `frontend/src/app.ts`
- Modify: `frontend/src/core/i18n.ts`
- Modify: `frontend/src/style.css`
- Create: `scripts/check_attention_today_contract.cjs`
- Modify: `frontend/package.json`

- [ ] **Step 1: Write failing static contract check**

Create `scripts/check_attention_today_contract.cjs`:

```javascript
#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const read = (rel) => fs.readFileSync(path.join(root, rel), "utf8");
const assertIncludes = (source, needle, message) => {
  if (!source.includes(needle)) throw new Error(message);
};

const models = read("frontend/src/core/models.ts");
const api = read("frontend/src/services/api.ts");
const layout = read("frontend/src/components/layout.ts");
const panel = read("frontend/src/components/attentionTodayPanel.ts");
const app = read("frontend/src/app.ts");
const styles = read("frontend/src/style.css");
const pkg = JSON.parse(read("frontend/package.json"));

assertIncludes(models, "export interface AttentionTodayResponse", "missing AttentionTodayResponse model");
assertIncludes(models, "export interface AttentionItem", "missing AttentionItem model");
assertIncludes(api, "export async function fetchAttentionTodayApi", "missing fetchAttentionTodayApi");
assertIncludes(layout, "attention-today-panel", "missing desktop Today panel anchor");
assertIncludes(layout, "attention-today-mobile-handle", "missing mobile Today handle");
assertIncludes(panel, "document.createElement", "Today panel must use DOM construction, not broad innerHTML templates");
assertIncludes(panel, "data-testid", "Today panel needs stable Playwright hooks");
assertIncludes(app, "initAttentionTodayPanel", "app must initialize Attention Today panel");
assertIncludes(app, "fetchAttentionTodayApi", "app must call Attention Today API");
assertIncludes(styles, ".attention-today-panel", "missing desktop panel styles");
assertIncludes(styles, "@media (prefers-reduced-motion: reduce)", "missing reduced motion handling");
if (!pkg.scripts || !pkg.scripts["check:attention-today"]) {
  throw new Error("missing check:attention-today package script");
}
```

Add `"check:attention-today": "node ../scripts/check_attention_today_contract.cjs"` to `frontend/package.json` and include it in the `build` chain before `tsc --noEmit`.

- [ ] **Step 2: Run the static check to verify RED**

Run:

```bash
cd frontend
npm run check:attention-today
```

Expected: FAIL because types, helpers, anchors, component, and app wiring do not exist.

- [ ] **Step 3: Add frontend models and API helpers**

In `frontend/src/core/models.ts`, add `AttentionAction`, `AttentionItem`, `AttentionSection`, `AttentionPreferences`, and `AttentionTodayResponse` matching the backend API contract.

In `frontend/src/services/api.ts`, add:

```typescript
export async function fetchAttentionTodayApi(): Promise<AttentionTodayResponse> {
  return apiGet<AttentionTodayResponse>("/api/attention/today");
}
```

Do not add preference mutation helpers until Task 9.

- [ ] **Step 4: Add layout anchors**

In `frontend/src/components/layout.ts`, inside `.map-layout`, after `.map-stage` and before `#shade-panel`, add the desktop panel, mobile handle, and mobile sheet anchors with stable `data-testid` attributes.

- [ ] **Step 5: Implement the panel with DOM construction**

Create `frontend/src/components/attentionTodayPanel.ts`. Use `document.createElement`, `textContent`, `setAttribute`, and event listeners. Do not render item title/body/reason/explanation with broad `innerHTML`.

The component must expose:

```typescript
export interface AttentionTodayPanelController {
  render(feed: AttentionTodayResponse | null): void;
  setLoading(): void;
  setError(message: string): void;
  refresh(): void;
}

export function initAttentionTodayPanel(options: AttentionTodayPanelOptions): AttentionTodayPanelController
```

It must render:

- Region heading "Today".
- Sections for Needs attention, Warnings, Coming up, and No action needed.
- No action needed collapsed by default.
- Desktop panel open by default.
- Mobile handle with `aria-expanded` and accessible label "Today, N items need attention".
- Explicit mobile close button that returns focus to the handle.
- Primary task action buttons with stable `data-testid`.
- Settings button labelled "Attention settings", disabled until Task 9, with title text "Attention settings are available after preferences are enabled".

- [ ] **Step 6: Wire app initialization and map/task action**

In `frontend/src/app.ts`, initialize the panel after `getAppShellMarkup()` and call `fetchAttentionTodayApi()`. For task primary actions, switch to Activity/Tasks and refresh the panel after existing task mutations. If plot ids are present, apply map highlight before navigation.

- [ ] **Step 7: Add focused styles and i18n**

Add CSS for responsive panel/sheet, 44px controls, focus-visible states, and reduced-motion no-slide behavior. Add English and Norwegian i18n keys for Today, Attention settings, No action needed, Some sources unavailable, Dismiss, Snooze, Close, and mobile handle copy.

- [ ] **Step 8: Run checks to verify GREEN**

Run:

```bash
cd frontend
npm run check:attention-today
npm run typecheck
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/core/models.ts frontend/src/services/api.ts frontend/src/components/layout.ts frontend/src/components/attentionTodayPanel.ts frontend/src/app.ts frontend/src/core/i18n.ts frontend/src/style.css scripts/check_attention_today_contract.cjs frontend/package.json
git commit -m "feat: add task-only map attention panel"
```

## Task 6: Full-Stack Task-Only Playwright Slice

**Files:**
- Create: `scripts/seed_attention_today_e2e.py`
- Create: `scripts/check_attention_today_e2e.cjs`
- Create: `scripts/run_attention_today_e2e.sh`
- Modify: `scripts/check_attention_e2e_db_safety.py`
- Modify: `frontend/package.json`

- [ ] **Step 1: Write the task-only deterministic seed**

Create `scripts/seed_attention_today_e2e.py` with:

```python
#!/usr/bin/env python3

from __future__ import annotations

import os

from psycopg import sql

import gardenops.db as db
from gardenops.security import create_user
from gardenops.services.attention import require_attention_e2e_database


def truncate_public_tables(conn) -> None:
    rows = conn.execute(
        """
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename != 'schema_migrations'
        """
    ).fetchall()
    tables = [row["tablename"] for row in rows]
    if tables:
        conn.execute(
            sql.SQL("TRUNCATE {} CASCADE").format(
                sql.SQL(", ").join(sql.Identifier(table) for table in tables),
            ),
        )


def main() -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    require_attention_e2e_database(database_url)
    db.run_migrations()
    conn = db.get_db()
    try:
        truncate_public_tables(conn)
        db.ensure_default_garden(conn)
        user = create_user(
            conn,
            username="attention_e2e_admin",
            password="AttentionE2E!Passphrase1234567890",  # push-sanitizer: allow SECRET_ASSIGNMENT test-only E2E user password
            role="admin",
        )
        garden_id = int(conn.execute("SELECT id FROM gardens WHERE slug = 'default'").fetchone()["id"])
        user_id = int(user["id"])
        conn.execute(
            "INSERT INTO garden_memberships (garden_id, user_id, role, created_at_ms, updated_at_ms) "
            "VALUES (%s, %s, 'admin', 1, 1) ON CONFLICT DO NOTHING",
            (garden_id, user_id),
        )
        conn.execute(
            "INSERT INTO plots (plot_id, garden_id, zone_code, zone_name, plot_number, grid_row, grid_col, sub_zone, notes) "
            "VALUES ('A1', %s, 'A', 'Beds', 1, 1, 1, '', '')",
            (garden_id,),
        )
        conn.execute(
            """
            INSERT INTO garden_tasks
            (public_id, garden_id, task_type, title, description, status, severity, due_on,
             rule_source, metadata_json, created_at_ms, updated_at_ms)
            VALUES ('task_basil_water', %s, 'water', 'Water basil', '', 'pending', 'normal', '2026-07-05',
                    '', '{}', 1, 1)
            """,
            (garden_id,),
        )
        task_id = int(conn.execute("SELECT id FROM garden_tasks WHERE public_id = 'task_basil_water'").fetchone()["id"])
        conn.execute("INSERT INTO garden_task_plots (task_id, plot_id) VALUES (%s, 'A1')", (task_id,))
        conn.commit()
    finally:
        db.return_db(conn)
        db.close_pool()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the DB safety check to verify GREEN**

Run:

```bash
python scripts/check_attention_e2e_db_safety.py
```

Expected: PASS because the seed script imports and calls `require_attention_e2e_database`.

- [ ] **Step 3: Write Playwright checks for the task-only slice**

Create `scripts/check_attention_today_e2e.cjs` that:

- Opens `BASE_URL`.
- Fails immediately if the script source contains route mock calls such as `page.route(` or `browserContext.route(`. Assemble those search needles from string pieces in the guard itself, for example `"page." + "route("`, so the guard does not match its own source. This journey must use real API calls.
- Asserts the Map tabpanel is visible before interacting with Today.
- Asserts `attention-today-panel` is visible on desktop.
- Asserts the panel width is no more than 360px, the map stage keeps at least 60% of viewport width, map controls are visible, and a pointer drag outside the panel still reaches the map stage.
- Asserts the Today region has role/name, the heading is unique on desktop, section toggles have `aria-expanded`/`aria-controls`, and touch targets are at least 44 CSS pixels.
- Asserts the seeded "Water basil" task appears.
- Clicks the task primary action and verifies Activity/Tasks navigation.
- Switches to mobile viewport, checks handle `aria-expanded=false`, opens it with Enter, checks `aria-expanded=true`, closes, and verifies focus returns to the handle.

Create `scripts/run_attention_today_e2e.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

: "${GARDENOPS_ATTENTION_E2E_TEST_URL:?Set GARDENOPS_ATTENTION_E2E_TEST_URL to a local disposable Postgres database}"

export APP_ENV=test
export AUTH_REQUIRED=false
export GARDENOPS_ATTENTION_E2E_ALLOW_TRUNCATE=1
export GARDENOPS_ATTENTION_FROZEN_NOW_MS=1783180800000
export GARDENOPS_ATTENTION_FROZEN_DATE=2026-07-05
export DATABASE_URL="$GARDENOPS_ATTENTION_E2E_TEST_URL"

uv run python scripts/seed_attention_today_e2e.py

backend_pid=""
frontend_pid=""
cleanup() {
  if [ -n "$frontend_pid" ]; then kill "$frontend_pid" 2>/dev/null || true; fi
  if [ -n "$backend_pid" ]; then kill "$backend_pid" 2>/dev/null || true; fi
}
trap cleanup EXIT

uv run uvicorn gardenops.main:app --host 127.0.0.1 --port 8000 &
backend_pid="$!"

for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:8000/api/health >/dev/null

(cd frontend && npm run dev -- --host 127.0.0.1 --port 5173) &
frontend_pid="$!"

for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:5173 >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:5173 >/dev/null

BASE_URL=http://127.0.0.1:5173 node scripts/check_attention_today_e2e.cjs
```

Add these scripts to `frontend/package.json`:

```json
"check:attention-today-e2e": "node ../scripts/check_attention_today_e2e.cjs",
"test:attention-today-e2e": "cd .. && scripts/run_attention_today_e2e.sh"
```

- [ ] **Step 4: Run Playwright to verify RED**

Prepare a disposable database and run the managed E2E command:

```bash
cd frontend
GARDENOPS_ATTENTION_E2E_TEST_URL="postgresql://localhost/gardenops_attention_e2e_test" npm run test:attention-today-e2e
```

Expected: FAIL until panel roles, stable test ids, action routing, mobile keyboard behavior, and focus restore are correct.

- [ ] **Step 5: Fix frontend and backend slice issues, then verify GREEN**

Run:

```bash
cd frontend
GARDENOPS_ATTENTION_E2E_TEST_URL="postgresql://localhost/gardenops_attention_e2e_test" npm run test:attention-today-e2e
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/seed_attention_today_e2e.py scripts/check_attention_today_e2e.cjs scripts/run_attention_today_e2e.sh scripts/check_attention_e2e_db_safety.py frontend/package.json
git commit -m "test: add task-only attention e2e slice"
```

## Task 7: Weather Provider And Rain-Aware Watering Outcomes

**Files:**
- Create: `gardenops/services/attention/providers/weather.py`
- Create: `gardenops/services/attention/outcomes.py`
- Modify: `gardenops/services/attention/providers/tasks.py`
- Modify: `gardenops/services/attention/service.py`
- Modify: `gardenops/services/attention/__init__.py`
- Modify: `gardenops/services/task_generator.py`
- Modify: `gardenops/services/notification_service.py`
- Modify: `tests/test_attention_api.py`
- Modify: `tests/test_attention_service_unit.py`
- Modify: `tests/test_task_generator.py`
- Modify: `tests/test_notifications.py`

- [ ] **Step 1: Write failing rain and exposure tests**

Add this unit test:

```python
def test_generated_watering_predicate_accepts_only_recognized_sources():
    from gardenops.services.attention import is_generated_watering_task

    assert is_generated_watering_task("water", "water:seasonal:PLT1")
    assert is_generated_watering_task("water", "auto:dry_water:alert:1")
    assert not is_generated_watering_task("water", "")
    assert not is_generated_watering_task("water", "manual")
    assert not is_generated_watering_task("prune", "water:seasonal:PLT1")
```

Add `tests/test_task_generator.py::test_rain_suppressed_watering_records_attention_outcome`. Seed an outdoor hydrangea that would normally generate a July watering task, seed an active `rain_surplus` alert covering `2026-07-05` with `{"rain_mm": 18}`, run the existing task generator for July 2026, and assert:

- no generated watering task is created for the covered date
- one `attention_outcomes` row exists with `outcome_type='watering_covered_by_rain'`
- the outcome uses the unique source key `(garden_id, provider='weather', outcome_type, source_type='task_generator', source_public_id=<water rule>, target_type='plant', target_id=<plt_id>)`
- the explanation includes `"18 mm rain"`

Add `TestAttentionRainWatering` that seeds:

- a persisted `watering_covered_by_rain` outcome
- an outdoor generated watering task that has a matching persisted outcome
- a generated indoor watering task
- a generated no-plot watering task
- an indoor manual watering task
- an active `rain_surplus` alert

Assert that only the generated outdoor task with a matching persisted outcome is absent from active sections, the indoor/manual/no-plot cases remain active, and No action needed contains `"18 mm rain"`.

Add a notification regression proving Attention read does not mutate `notification_events`: capture row count plus `dismissed`, `cleared_at_ms`, `clear_reason`, and `superseded_by_id` before and after `GET /api/attention/today`. Add a separate `tests/test_notifications.py` case proving existing task/weather notification maintenance clears generated watering notifications only when the task/weather domain rules say they are stale; this is not done by Attention.

- [ ] **Step 2: Run rain tests to verify RED**

Run:

```bash
uv run pytest tests/test_attention_service_unit.py::test_generated_watering_predicate_accepts_only_recognized_sources tests/test_task_generator.py::test_rain_suppressed_watering_records_attention_outcome tests/test_attention_api.py::TestAttentionRainWatering tests/test_notifications.py::TestRainSuppressedWateringNotificationLifecycle -q
```

Expected: FAIL because rain suppression and weather outcomes are not wired in.

- [ ] **Step 3: Implement outcome helpers and task-generator outcome writes**

Create `gardenops/services/attention/outcomes.py` with idempotent upsert/read helpers for `attention_outcomes`, retention, and safe restore metadata. `upsert_attention_outcome()` must use `ux_attention_outcomes_source_kind` as the conflict key and must update `explanation`, `metadata_json`, `occurred_at_ms`, and `expires_at_ms` instead of creating duplicates.

Modify `gardenops/services/task_generator.py` so the existing `_rain_covers_date()` branch that skips weekly generated watering also calls `upsert_attention_outcome()`. This write happens at the domain automation point where the watering task is suppressed, not during `GET /api/attention/today`.

Modify `gardenops/services/notification_service.py` only to reuse existing stale-notification maintenance for generated rain-suppressed task notifications. Do not call any notification clear/update helper from `gardenops/services/attention/*`.

- [ ] **Step 4: Implement weather provider and read-only task suppression**

Create `gardenops/services/attention/providers/weather.py` with active non-dismissed `weather_alerts` mapping and rain helper functions:

```python
RAIN_COVERS_WATERING_MM = 10.0


def is_generated_watering_task(task_type: str, rule_source: str | None) -> bool:
    value = (rule_source or "").strip()
    return task_type == "water" and (value.startswith("water:") or value.startswith("auto:dry_water:"))
```

Update `TaskAttentionProvider` so generated outdoor watering is omitted from active Attention only when there is already a matching persisted `watering_covered_by_rain` or `watering_rescheduled_by_rain` outcome. Keep manual, generated indoor, covered/ambiguous, and no-plot watering active unless domain automation has already written a matching outcome. Do not write `attention_outcomes` from `TaskAttentionProvider`.

Update `AttentionService` to include `WeatherAttentionProvider` after the task-only Playwright slice is already passing.

- [ ] **Step 5: Add degraded-provider tests**

Add `TestAttentionProviderDegradation` to `tests/test_attention_api.py`:

```python
class TestAttentionProviderDegradation(BaseApiTest):
    def test_provider_failure_returns_degraded_provider(self) -> None:
        r = self.client.get("/api/attention/today?force_degraded_provider=weather")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["degraded_providers"], [{"provider": "weather", "status": "degraded"}])

    def test_sql_provider_failure_does_not_abort_later_provider(self) -> None:
        from gardenops.services.attention import AttentionService, TaskAttentionProvider

        class BrokenSqlProvider:
            key = "weather"

            def collect(self, conn, *, garden_id: int, user_id: int, now_ms: int):
                conn.execute("SELECT * FROM attention_missing_table_for_savepoint_test")
                return []

        conn = db.get_db()
        try:
            garden_id = int(conn.execute("SELECT id FROM gardens WHERE slug = 'default'").fetchone()["id"])
            user_id = int(conn.execute("SELECT id FROM auth_users WHERE username = 'test_admin'").fetchone()["id"])
            body = AttentionService(
                providers=[
                    BrokenSqlProvider(),
                    TaskAttentionProvider(frozen_date="2026-07-05"),
                ],
            ).today(conn, garden_id=garden_id, user_id=user_id, now_ms=1783180800000)
        finally:
            db.return_db(conn)

        self.assertEqual(body["degraded_providers"], [{"provider": "weather", "status": "degraded"}])
        self.assertIn("sections", body)
```

- [ ] **Step 6: Implement the test-only degradation hook**

The `force_degraded_provider` query parameter must work only when `APP_ENV=test`. In `gardenops/routers/attention.py`, add the test-only query parameter at this task, not earlier:

```python
if force_degraded_provider and os.environ.get("APP_ENV", "").strip().lower() != "test":
    raise HTTPException(status_code=400, detail="Unsupported query parameter")
```

- [ ] **Step 7: Run weather tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_attention_service_unit.py::test_generated_watering_predicate_accepts_only_recognized_sources tests/test_task_generator.py::test_rain_suppressed_watering_records_attention_outcome tests/test_attention_api.py::TestAttentionRainWatering tests/test_attention_api.py::TestAttentionProviderDegradation tests/test_notifications.py::TestRainSuppressedWateringNotificationLifecycle -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add gardenops/services/attention/__init__.py gardenops/services/attention/outcomes.py gardenops/services/attention/providers/weather.py gardenops/services/attention/providers/tasks.py gardenops/services/attention/service.py gardenops/services/task_generator.py gardenops/services/notification_service.py tests/test_attention_api.py tests/test_attention_service_unit.py tests/test_task_generator.py tests/test_notifications.py
git commit -m "feat: add weather-aware attention outcomes"
```

## Task 8: Issue, Calendar, And Read-Only Notification Providers

**Files:**
- Create: `gardenops/services/attention/providers/issues.py`
- Create: `gardenops/services/attention/providers/calendar.py`
- Create: `gardenops/services/attention/providers/notifications.py`
- Modify: `gardenops/services/attention/service.py`
- Modify: `gardenops/services/attention/__init__.py`
- Modify: `tests/test_attention_api.py`

- [ ] **Step 1: Write failing provider tests**

Add tests proving:

- Open high-severity issue and overdue issue follow-up appear before routine due tasks.
- Resolved issues from the last 24 hours appear in No action needed.
- Manual calendar event due today appears only when it is not a duplicate of a task/weather/issue item.
- Legacy `notification_events` system/status rows can be adapted into attention.
- Legacy notification provider does not update, clear, dismiss, supersede, or delete `notification_events`.

The read-only notification test must capture row count plus `dismissed`, `cleared_at_ms`, `clear_reason`, and `superseded_by_id` values before and after provider collection.

- [ ] **Step 2: Run provider tests to verify RED**

Run:

```bash
uv run pytest tests/test_attention_api.py::TestAttentionExpandedProviders -q
```

Expected: FAIL because issue, calendar, and notification providers do not exist.

- [ ] **Step 3: Implement issue provider**

Create `gardenops/services/attention/providers/issues.py`. Map:

- `status='open'` with high/critical severity to `warning` or `needs_action`.
- `follow_up_on < frozen_date` to `issue_follow_up_overdue`.
- `follow_up_on == frozen_date` to `issue_follow_up_due`.
- recently resolved issues to `no_action_needed`.

Include linked plot and plant metadata for map context.

- [ ] **Step 4: Implement calendar provider**

Create `gardenops/services/attention/providers/calendar.py`. It should load `garden_calendar_events` for frozen date through seven days ahead, skip records whose metadata links to an already emitted task/weather/issue item, and emit `upcoming` or `needs_action` based on date and severity.

- [ ] **Step 5: Implement read-only notification/status provider**

Create `gardenops/services/attention/providers/notifications.py`. It may select active system/status rows from `notification_events`, but must not call `commit()`, `UPDATE`, `DELETE`, clear helpers, scheduler helpers, or notification maintenance helpers.

- [ ] **Step 6: Wire providers into service after task/weather**

Update `AttentionService` provider order:

```python
TaskAttentionProvider(frozen_date=frozen_date),
WeatherAttentionProvider(frozen_date=frozen_date),
IssueAttentionProvider(frozen_date=frozen_date),
CalendarAttentionProvider(frozen_date=frozen_date),
NotificationStatusAttentionProvider(),
```

- [ ] **Step 7: Run provider tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_attention_api.py::TestAttentionExpandedProviders tests/test_notifications.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add gardenops/services/attention/__init__.py gardenops/services/attention/providers/issues.py gardenops/services/attention/providers/calendar.py gardenops/services/attention/providers/notifications.py gardenops/services/attention/service.py tests/test_attention_api.py
git commit -m "feat: add expanded attention providers"
```

## Task 9: Preferences, User State, Restore Guardrails, And Settings UI

**Files:**
- Modify: `gardenops/services/attention/preferences.py`
- Modify: `gardenops/services/attention/service.py`
- Modify: `gardenops/services/attention/outcomes.py`
- Modify: `gardenops/routers/attention.py`
- Modify: `frontend/src/core/models.ts`
- Modify: `frontend/src/services/api.ts`
- Modify: `frontend/src/components/attentionTodayPanel.ts`
- Modify: `frontend/src/app.ts`
- Modify: `frontend/src/core/i18n.ts`
- Modify: `tests/test_attention_api.py`

- [ ] **Step 1: Write failing backend mutation and preference tests**

Add tests for:

- `GET /api/attention/preferences` returns Balanced defaults for new users.
- Existing notification preferences migrate to a Custom preset without disabling Map panel visibility by default.
- `PUT /api/attention/preferences` enforces high/critical safety, frost, security, and system guardrails.
- Calm, Balanced, and Detailed presets produce different panel/inbox/digest eligibility for routine task, warning, and system items.
- `POST /api/attention/items/{item_id}/read`, `/dismiss`, and `/snooze` are user-scoped.
- Home-tier users receive `403` for `GET /api/attention/today`, `PUT /api/attention/preferences`, and `POST /api/attention/items/{item_id}/read`; enthusiast/pro users reach the real endpoint behavior.
- Unsupported `/restore` returns 409.
- Supported watering restore validates `attention_outcomes.recovery_action_json` and delegates to existing task/weather services without directly writing notification rows.

- [ ] **Step 2: Run backend tests to verify RED**

Run:

```bash
uv run pytest tests/test_attention_api.py::TestAttentionMutations tests/test_attention_api.py::TestAttentionPreferences -q
```

Expected: FAIL until endpoints and guardrails are implemented.

- [ ] **Step 3: Implement preference and user-state endpoints**

In `gardenops/routers/attention.py`, add strict bodies for preferences and snooze. In service/preference modules, implement:

- `load_attention_preferences()`
- `save_attention_preferences()`
- `set_user_attention_state()`
- `load_user_attention_states()`
- `restore_attention_outcome()`

`restore_attention_outcome()` is an orchestrator. It validates the outcome and delegates to existing domain services. It must not directly mutate task, issue, weather, or notification tables except through those domain services.

- [ ] **Step 4: Add frontend API helpers**

In `frontend/src/services/api.ts`, add:

```typescript
export async function fetchAttentionPreferencesApi(): Promise<AttentionPreferences> {
  return apiGet<AttentionPreferences>("/api/attention/preferences");
}

export async function updateAttentionPreferencesApi(body: Partial<AttentionPreferences>): Promise<AttentionPreferences> {
  const response = await checked(
    await apiFetch("/api/attention/preferences", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
    "/api/attention/preferences",
  );
  return (await response.json()) as AttentionPreferences;
}

export async function markAttentionItemReadApi(itemId: string): Promise<AttentionItem> {
  return apiPost<AttentionItem>(`/api/attention/items/${encodeApiPathSegment(itemId)}/read`, {});
}

export async function dismissAttentionItemApi(itemId: string): Promise<AttentionItem> {
  return apiPost<AttentionItem>(`/api/attention/items/${encodeApiPathSegment(itemId)}/dismiss`, {});
}

export async function snoozeAttentionItemApi(itemId: string, snoozedUntilMs: number): Promise<AttentionItem> {
  return apiPost<AttentionItem>(`/api/attention/items/${encodeApiPathSegment(itemId)}/snooze`, { snoozed_until_ms: snoozedUntilMs });
}

export async function restoreAttentionItemApi(itemId: string): Promise<AttentionItem> {
  return apiPost<AttentionItem>(`/api/attention/items/${encodeApiPathSegment(itemId)}/restore`, {});
}
```

- [ ] **Step 5: Implement settings UI**

In `attentionTodayPanel.ts`, make Attention settings open a compact dialog with Calm, Balanced, Detailed, and Custom. It must load the current preference state before showing editable controls, support Save and Cancel, keep focus inside the dialog, restore focus to the settings button on close, refresh the Today feed after Save, and show an inline guardrail explanation when a muted category still appears because high/critical safety, frost, security, or system rules apply.

- [ ] **Step 6: Run backend and frontend checks**

Run:

```bash
uv run pytest tests/test_attention_api.py::TestAttentionMutations tests/test_attention_api.py::TestAttentionPreferences -q
cd frontend
npm run check:attention-today
npm run typecheck
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add gardenops/services/attention/preferences.py gardenops/services/attention/service.py gardenops/services/attention/outcomes.py gardenops/routers/attention.py frontend/src/core/models.ts frontend/src/services/api.ts frontend/src/components/attentionTodayPanel.ts frontend/src/app.ts frontend/src/core/i18n.ts tests/test_attention_api.py
git commit -m "feat: add attention preferences and user state"
```

## Task 10: Full-Stack Playwright Morning Garden Check Journey

**Files:**
- Modify: `scripts/seed_attention_today_e2e.py`
- Modify: `scripts/check_attention_today_e2e.cjs`
- Modify: `scripts/run_attention_today_e2e.sh`
- Modify: `frontend/package.json`
- Modify: `scripts/check_attention_today_contract.cjs`

- [ ] **Step 1: Expand the guarded deterministic seed**

Modify `scripts/seed_attention_today_e2e.py`. Keep `require_attention_e2e_database(database_url)` before any truncate or write. Add:

- outdoor hydrangea task-generator input that would create watering on `2026-07-05`, but records a `watering_covered_by_rain` outcome instead because rain covers that date
- indoor basil manual watering due `2026-07-05`
- active `rain_surplus` weather alert with `18 mm`
- open high-severity issue follow-up due `2026-07-04`
- one non-duplicated manual calendar event
- one legacy system/status notification unrelated to the suppressed watering outcome

- [ ] **Step 2: Expand the Playwright script**

Modify `scripts/check_attention_today_e2e.cjs` so it now asserts:

- Map is still the first visual surface.
- Today panel is open and compact on desktop.
- Outdoor generated watering is absent from active attention because task/weather automation wrote a persisted no-action outcome before Attention read the feed.
- Indoor manual watering remains actionable.
- No action needed is collapsed by default, then expands to show the `18 mm rain expected` explanation.
- Issue follow-up primary action navigates to Activity/Issues and map context can be restored.
- Notification inbox does not show an active generated watering notification because the domain seed/maintenance did not create or already cleared one; the test also captures `notification_events` before/after opening Today and proves Attention did not mutate those rows.
- Attention settings opens a dialog, traps focus, shows Calm/Balanced/Detailed/Custom, Cancel preserves state, Save refreshes Today, and guardrail explanation copy appears when a protected category remains visible.
- Mobile handle starts collapsed, opens with Enter, closes with Close, and focus returns to the handle.

Keep the task-only assertions from Task 6.

- [ ] **Step 3: Run Playwright to verify RED**

Run the managed E2E command:

```bash
cd frontend
GARDENOPS_ATTENTION_E2E_TEST_URL="postgresql://localhost/gardenops_attention_e2e_test" npm run test:attention-today-e2e
```

Expected: FAIL until panel roles, stable test ids, action routing, and mobile focus behavior are correct.

- [ ] **Step 4: Fix frontend accessibility and deterministic hooks**

Update `attentionTodayPanel.ts` so:

- Desktop and mobile render different heading IDs.
- The mobile sheet has an explicit Close button.
- Closing mobile sheet returns focus to `#attention-today-mobile-handle`.
- Section toggles use `aria-expanded` and `aria-controls`.
- No action needed is collapsed by default.
- Primary action buttons have stable `data-testid="attention-primary-{safeItemId}"`.
- Reduced-motion mode disables sheet slide animation.
- Settings dialog has `role="dialog"`, an accessible name, Save and Cancel buttons, focus trap, and focus restore.
- Touch targets are at least 44 CSS pixels.

- [ ] **Step 5: Run Playwright to verify GREEN**

Run:

```bash
cd frontend
GARDENOPS_ATTENTION_E2E_TEST_URL="postgresql://localhost/gardenops_attention_e2e_test" npm run test:attention-today-e2e
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/seed_attention_today_e2e.py scripts/check_attention_today_e2e.cjs scripts/run_attention_today_e2e.sh frontend/package.json frontend/src/components/attentionTodayPanel.ts frontend/src/style.css
git commit -m "test: add attention today e2e journey"
```

## Task 11: Backend Full-Journey Regression

**Files:**
- Modify: `tests/test_attention_api.py`

- [ ] **Step 1: Add the Morning Garden Check backend test**

Add `TestAttentionMorningGardenCheck` that seeds:

- outdoor hydrangea task-generator input plus rain outcome for watering covered on `2026-07-05`
- indoor basil manual watering due `2026-07-05`
- active `rain_surplus` with `18 mm`
- open high-severity issue follow-up due `2026-07-04`
- a legacy notification row, then captures it before and after `GET /api/attention/today`

Assert:

```python
body = self.client.get("/api/attention/today").json()
active_titles = [
    item["title"]
    for section in body["sections"]
    if section["key"] != "no_action_needed"
    for item in section["items"]
]
no_action_titles = [
    item["title"]
    for section in body["sections"]
    if section["key"] == "no_action_needed"
    for item in section["items"]
]

self.assertIn("Check mildew on cucumber", active_titles)
self.assertIn("Water indoor basil", active_titles)
self.assertNotIn("Water hydrangea", active_titles)
self.assertTrue(any("Watering" in title for title in no_action_titles))
self.assertEqual(notification_rows_before, notification_rows_after)
```

- [ ] **Step 2: Run the backend journey test**

Run:

```bash
uv run pytest tests/test_attention_api.py::TestAttentionMorningGardenCheck -q
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_attention_api.py
git commit -m "test: cover morning garden attention journey"
```

## Task 12: Final Validation, Documentation, And Release Notes

**Files:**
- Modify `README.md` when the documentation-upkeep inventory flags the new user-facing panel.
- Modify no public docs when the implementation branch is still behind a feature gate and the inventory returns no user-facing docs requirement.

- [ ] **Step 1: Run backend focused tests**

Run:

```bash
uv run pytest tests/test_attention_service_unit.py tests/test_attention_api.py tests/test_notifications.py tests/test_task_generator.py tests/test_scheduler_automation.py tests/test_weather.py tests/test_issues.py tests/test_calendar.py -q
python scripts/check_attention_e2e_db_safety.py
```

Expected: PASS.

- [ ] **Step 2: Run frontend static, type, and build checks**

Run:

```bash
cd frontend
npm run check:attention-today
npm run typecheck
npm run build
```

Expected: PASS.

- [ ] **Step 3: Run full-stack Playwright journey**

Run:

```bash
cd frontend
GARDENOPS_ATTENTION_E2E_TEST_URL="postgresql://localhost/gardenops_attention_e2e_test" npm run test:attention-today-e2e
```

Expected: PASS.

- [ ] **Step 4: Run documentation upkeep**

Run:

```bash
python .codex/skills/gardenops-documentation-upkeep/scripts/docs_impact_inventory.py
```

If the inventory flags `README.md` for a user-visible feature, add one short feature bullet:

```markdown
- Map Today attention panel summarizes current tasks, issue follow-ups, weather risks, and no-action-needed automation outcomes without replacing the map-first workflow.
```

- [ ] **Step 5: Run repository hygiene**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors. The status should include only intentional Attention implementation files and any pre-existing unrelated local files.

- [ ] **Step 6: Commit validation/docs changes**

```bash
git add README.md docs/superpowers/plans/2026-07-05-attention-model-map-today-implementation.md
git commit -m "docs: document attention today rollout"
```

Include `README.md` in this commit only when it changed during Step 4.

## Acceptance Checklist

- [x] Map remains the first visual surface on desktop.
- [x] Desktop Today panel is compact and open by default.
- [x] Playwright proves the panel is capped, does not overlap map controls, and pointer interaction outside the panel still reaches the map.
- [x] Mobile Today starts as a bottom handle with `aria-expanded=false`.
- [x] No action needed is collapsed by default and expandable.
- [x] Generated outdoor watering covered by rain is suppressed by task/weather automation, persisted as an `attention_outcomes` row, and then appears only as No action needed.
- [x] Manual, indoor, covered, and ambiguous watering remain actionable unless domain automation has a persisted suppression/reschedule outcome.
- [x] One user's dismiss/snooze does not hide the item for other users.
- [x] High/critical safety, frost, security, and system guardrails prevent hiding from every non-email surface.
- [x] Calm, Balanced, Detailed, and Custom preferences change delivery eligibility and are covered by backend and Playwright tests.
- [x] Custom Attention settings expose category/channel matrix controls, quiet-hour controls, and watering/weather preference metadata.
- [x] Notification inbox and email digest delivery consult Attention preference eligibility without mutating notification log rows.
- [x] Attention phase 1 does not mutate existing `notification_events`; notification inbox absence for rain-suppressed watering is caused by task/weather notification maintenance, not the Attention read endpoint.
- [x] Provider failure returns usable partial feed plus `degraded_providers`.
- [x] Playwright covers desktop, keyboard, reduced motion, labelled regions/dialogs, no-action expansion, settings Save/Cancel, action navigation, and mobile handle/sheet behavior.
- [x] Frontend build and backend focused tests pass.

## Self-Review Notes

- Spec coverage: tasks above cover storage, lifecycle vocabulary, providers, preferences, Map Today panel, rain suppression, notification inbox/digest preference filtering, API shape, accessibility, backend tests, and full-stack Playwright journey. Notification lifecycle ownership remains with existing notification tables; the Attention adapter filters delivery without rewriting inbox/log rows.
- Placeholder scan: this plan avoids unsupported future providers and keeps every code task tied to concrete files, commands, and expected outcomes.
- Type consistency: backend item fields and frontend `AttentionItem` fields use the same names as the spec and API response.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-05-attention-model-map-today-implementation.md`.

Two execution options:

1. **Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, and keep commits small.
2. **Inline Execution** - execute tasks in this session with checkpoints after each backend, frontend, and E2E phase.
