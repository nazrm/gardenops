# Task Completion History And Snooze Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make horticultural task snooze and completion behave as durable garden history: bloom/prune/fertilize snooze defaults are consistent, completion logs journal events, grouped tasks require plant selection, and bloom "not yet" becomes local timing evidence.

**Architecture:** Add a small task-domain service layer beside the existing task router. The backend owns completion capture, idempotency, task-link narrowing, bloom not-yet metadata, and notification refresh; the frontend owns task-type snooze policy, completion-selection UI, and accessible toast/date flows. No schema migration is required for the first slice because journal event types, task metadata, and task-plant links already exist.

**Tech Stack:** FastAPI, Pydantic strict request models, PostgreSQL via the existing GardenOps DB wrapper, vanilla TypeScript, Vite, Playwright Core scripts, pytest.

---

## Review Summary Applied

Adversarial review findings folded into the spec and this plan:

- Batch completion must not silently complete multi-plant horticultural tasks without plant selection.
- Completion capture must be idempotent across retries/offline replay.
- Partial completion must refresh task notifications because existing notification metadata includes plant names.

Horticultural review findings folded into the spec and this plan:

- Bloom `Not seen this season` means not observed blooming, not not seen growing; it must not mark presence as gone.
- Prune/fertilize `+1 week` is safe as a default only inside the task care window; if it exceeds `window_end_on`, prompt with a warning.
- Completion notes should flow into journal entries because treatment details matter for pruning and fertilizing history.

## File Structure

- Create `gardenops/services/task_completion.py`
  - Owns completion-capture mapping, selected plant validation, journal creation, idempotency metadata, partial completion link narrowing, generated work-order title refresh, and bloom not-yet metadata.
- Modify `gardenops/routers/tasks.py`
  - Extend `ActionTaskBody` with `completed_plant_ids` and `completion_outcome`.
  - Delegate complete/snooze side effects into `task_completion.py`.
  - Keep transaction boundaries in the router.
- Modify `gardenops/services/notification_service.py` only if a small helper is needed to regenerate task notifications without committing inside task action transactions.
  - Prefer adding a non-committing helper used by both `create_task_due_notifications` and task completion refresh.
- Modify `tests/test_tasks.py`
  - Backend contract, completion capture, idempotency, partial completion, bloom not-seen outcome, and batch validation.
- Modify `tests/test_notifications.py`
  - Notification refresh after partial completion.
- Create `frontend/src/features/taskSnoozePolicy.ts`
  - Pure helper for default dates, labels, and window warnings.
- Create `frontend/src/features/taskCompletionFlow.ts`
  - Completion selection sheet and shared complete/snooze action flow for task surfaces.
- Modify `frontend/src/services/api.ts`
  - Extend `TaskActionRequest` with completion target fields.
- Modify `frontend/src/core/appContext.ts`
  - Allow toast action metadata if the shared toast action path needs context-level access.
- Modify `frontend/src/components/toast.ts`
  - Add optional action buttons with keyboard support, hover/focus pause, and longer duration for actionable toast.
- Modify `frontend/src/tabs/tasksTab.ts`
  - Use shared completion flow, shared snooze policy, batch grouped-task guard, and date warning dialog.
- Modify `frontend/src/tabs/calendarTab.ts`
  - Use shared snooze policy and completion flow from calendar task details.
- Modify `frontend/src/components/plotInteractions.ts`
  - Use shared snooze policy for drawer task snooze.
  - Keep completion one-tap for single-plant tasks; open selection for grouped tasks if enough context is available.
- Modify `frontend/src/features/quickActionsFeature.ts`
  - Replace `+1 day` hard-code with shared snooze policy.
- Modify `frontend/src/components/tasks.ts`
  - Surface task-specific action labels when provided by callbacks or model helper.
- Modify `frontend/src/core/i18n.ts`
  - Add English and Norwegian labels for completion selection, snooze confirmation, window warning, and not-seen bloom outcome.
- Create `scripts/check_task_snooze_policy_contract.cjs`
  - Static frontend contract check for mapped task types and date helpers.
- Modify `frontend/package.json`
  - Add the new contract check to `npm run build`.
- Create `scripts/seed_task_completion_history_e2e.py`
  - Disposable E2E seed for bloom/prune/fertilize journeys.
- Create `scripts/check_task_completion_history_e2e.cjs`
  - Playwright Core journey for snooze, grouped completion, partial completion, journal history, and keyboard flow.
- Create `scripts/run_task_completion_history_e2e.sh`
  - Starts FastAPI and Vite against a disposable database, then runs the Playwright script.
- Modify `docs/development.md`
  - Mention the new task-history E2E command next to the existing Attention E2E command.

## Task 1: Backend Contract And RED Tests

**Files:**
- Modify: `gardenops/routers/tasks.py`
- Test: `tests/test_tasks.py`

- [ ] **Step 1: Add failing tests for task action body and grouped validation**

Add tests to `tests/test_tasks.py` under `class TestTasks`:

```python
def test_grouped_horticultural_completion_requires_selected_plants(self) -> None:
    response = self.client.post(
        "/api/tasks",
        json={
            "task_type": "fertilize",
            "title": "Fertilize two plants",
            "due_on": "2026-06-01",
            "plant_ids": ["PLT-TEST", "PLT-002"],
        },
    )
    self.assertEqual(response.status_code, 201, response.text)
    task_id = response.json()["id"]

    response = self.client.post(f"/api/tasks/{task_id}/action", json={"action": "complete"})

    self.assertEqual(response.status_code, 422)
    self.assertIn("completed_plant_ids", response.text)


def test_non_horticultural_completion_rejects_selected_plants(self) -> None:
    response = self.client.post(
        "/api/tasks",
        json={
            "task_type": "inspect_issue",
            "title": "Inspect aphids",
            "due_on": "2026-06-01",
            "plant_ids": ["PLT-TEST"],
        },
    )
    self.assertEqual(response.status_code, 201, response.text)
    task_id = response.json()["id"]

    response = self.client.post(
        f"/api/tasks/{task_id}/action",
        json={"action": "complete", "completed_plant_ids": ["PLT-TEST"]},
    )

    self.assertEqual(response.status_code, 422)
    self.assertIn("completion capture", response.text)
```

- [ ] **Step 2: Run contract tests and verify RED**

Run:

```bash
uv run pytest tests/test_tasks.py::TestTasks::test_grouped_horticultural_completion_requires_selected_plants tests/test_tasks.py::TestTasks::test_non_horticultural_completion_rejects_selected_plants -q
```

Expected: both fail because `completed_plant_ids` is not part of `ActionTaskBody` yet.

- [ ] **Step 3: Extend the action request model minimally**

In `gardenops/routers/tasks.py`, change `ActionTaskBody` to:

```python
CompletionOutcome = Literal["done", "not_seen_blooming_this_season"]


class ActionTaskBody(StrictBaseModel):
    action: Literal["complete", "skip", "snooze", "reschedule"]
    snooze_until: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    reschedule_to: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    notes: str | None = Field(default=None, max_length=2000)
    completed_plant_ids: list[str] | None = None
    completion_outcome: CompletionOutcome = "done"
```

Update `BatchActionTaskBody` inheritance unchanged.

- [ ] **Step 4: Add temporary validation inside `_apply_task_action`**

Before the existing complete update block, add a narrow validation guard:

```python
if body.action == "complete":
    task_type = str(task_row.get("task_type") or "")
    linked_plant_ids = _task_linked_plant_ids(db, task_id)
    if body.completed_plant_ids and task_type not in {"observe_bloom", "prune", "fertilize"}:
        raise HTTPException(
            status_code=422,
            detail="completed_plant_ids is only supported for task types with completion capture",
        )
    if task_type in {"observe_bloom", "prune", "fertilize"} and len(linked_plant_ids) > 1:
        selected = body.completed_plant_ids or []
        if not selected:
            raise HTTPException(
                status_code=422,
                detail="completed_plant_ids is required for grouped horticultural completion",
            )
```

This is intentionally not the final implementation; it gets the contract tests green before the service extraction.

- [ ] **Step 5: Run contract tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_tasks.py::TestTasks::test_grouped_horticultural_completion_requires_selected_plants tests/test_tasks.py::TestTasks::test_non_horticultural_completion_rejects_selected_plants -q
```

Expected: both pass.

- [ ] **Step 6: Commit**

```bash
git add gardenops/routers/tasks.py tests/test_tasks.py
git commit -m "test: define horticultural task completion contract"
```

## Task 2: Completion Capture Service

**Files:**
- Create: `gardenops/services/task_completion.py`
- Modify: `gardenops/routers/tasks.py`
- Test: `tests/test_tasks.py`

- [ ] **Step 1: Add failing tests for prune/fertilize journal capture**

Add tests:

```python
def test_prune_completion_creates_selected_plant_journal_entry(self) -> None:
    response = self.client.post(
        "/api/tasks",
        json={
            "task_type": "prune",
            "title": "Prune rose",
            "due_on": "2026-06-01",
            "plant_ids": ["PLT-002"],
            "plot_ids": ["B2"],
        },
    )
    self.assertEqual(response.status_code, 201, response.text)
    task_id = response.json()["id"]

    response = self.client.post(
        f"/api/tasks/{task_id}/action",
        json={"action": "complete", "notes": "Removed dead stems"},
    )
    self.assertEqual(response.status_code, 200, response.text)

    journal = self.client.get("/api/journal?event_type=pruned&plant_id=PLT-002").json()
    self.assertEqual(journal["total"], 1)
    entry = journal["entries"][0]
    self.assertEqual(entry["plant_ids"], ["PLT-002"])
    self.assertEqual(entry["metadata"]["source"], "task_completion")
    self.assertEqual(entry["metadata"]["source_task_id"], task_id)
    self.assertEqual(entry["metadata"]["source_task_type"], "prune")
    self.assertIn("Removed dead stems", entry["notes"])


def test_fertilize_completion_creates_selected_plant_journal_entry(self) -> None:
    response = self.client.post(
        "/api/tasks",
        json={
            "task_type": "fertilize",
            "title": "Feed rose",
            "due_on": "2026-06-01",
            "plant_ids": ["PLT-002"],
        },
    )
    self.assertEqual(response.status_code, 201, response.text)
    task_id = response.json()["id"]

    response = self.client.post(f"/api/tasks/{task_id}/action", json={"action": "complete"})
    self.assertEqual(response.status_code, 200, response.text)

    journal = self.client.get("/api/journal?event_type=fertilized&plant_id=PLT-002").json()
    self.assertEqual(journal["total"], 1)
    self.assertEqual(journal["entries"][0]["metadata"]["source_task_type"], "fertilize")
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest tests/test_tasks.py::TestTasks::test_prune_completion_creates_selected_plant_journal_entry tests/test_tasks.py::TestTasks::test_fertilize_completion_creates_selected_plant_journal_entry -q
```

Expected: fail because prune/fertilize completion does not create journal entries.

- [ ] **Step 3: Create `task_completion.py` with mappings and helpers**

Create `gardenops/services/task_completion.py`:

```python
from __future__ import annotations

import json
from datetime import date
from typing import Any, Literal

from fastapi import HTTPException

from gardenops.db import DbConn, executemany
from gardenops.router_helpers import generate_public_id
from gardenops.security import AuthContext
from gardenops.services.observation_updates import mark_seen_growing_from_observation

CompletionOutcome = Literal["done", "not_seen_blooming_this_season"]

COMPLETION_EVENT_BY_TASK_TYPE = {
    "observe_bloom": "bloomed",
    "prune": "pruned",
    "fertilize": "fertilized",
}


def is_completion_capture_task(task_type: str) -> bool:
    return task_type in COMPLETION_EVENT_BY_TASK_TYPE


def parse_task_metadata(task_row: dict[str, Any]) -> dict[str, Any]:
    raw = task_row.get("metadata_json")
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def completion_capture_key(
    *,
    task_public_id: str,
    event_type: str,
    outcome: CompletionOutcome,
    plant_ids: list[str],
) -> str:
    plants_key = ",".join(sorted(plant_ids))
    return f"{task_public_id}:{event_type}:{outcome}:{plants_key}"
```

- [ ] **Step 4: Add selected plant validation**

In the same file:

```python
def validate_completed_plant_ids(
    *,
    task_type: str,
    linked_plant_ids: list[str],
    requested_plant_ids: list[str] | None,
) -> list[str]:
    if requested_plant_ids and not is_completion_capture_task(task_type):
        raise HTTPException(
            status_code=422,
            detail="completed_plant_ids is only supported for task types with completion capture",
        )
    if not is_completion_capture_task(task_type):
        return []
    if not linked_plant_ids:
        return []
    if len(linked_plant_ids) == 1 and requested_plant_ids is None:
        return linked_plant_ids
    requested = []
    seen = set()
    for raw in requested_plant_ids or []:
        value = str(raw).strip()
        if value and value not in seen:
            requested.append(value)
            seen.add(value)
    if not requested:
        raise HTTPException(
            status_code=422,
            detail="completed_plant_ids is required for grouped horticultural completion",
        )
    linked = set(linked_plant_ids)
    invalid = [plant_id for plant_id in requested if plant_id not in linked]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"completed_plant_ids must be linked to the task: {', '.join(invalid[:5])}",
        )
    return requested
```

- [ ] **Step 5: Add idempotent journal creation**

In the same file:

```python
def record_completion_journal_entry(
    db: DbConn,
    *,
    context: AuthContext,
    task_row: dict[str, Any],
    selected_plant_ids: list[str],
    selected_plot_ids: list[str],
    outcome: CompletionOutcome,
    notes: str | None,
    now_ms: int,
) -> tuple[str | None, dict[str, Any]]:
    task_type = str(task_row.get("task_type") or "")
    if not selected_plant_ids or not is_completion_capture_task(task_type):
        return None, parse_task_metadata(task_row)

    event_type = COMPLETION_EVENT_BY_TASK_TYPE[task_type]
    if task_type == "observe_bloom" and outcome == "not_seen_blooming_this_season":
        event_type = "observed"

    metadata = parse_task_metadata(task_row)
    completion_records = metadata.setdefault("completion_journal_entries", {})
    key = completion_capture_key(
        task_public_id=str(task_row["public_id"]),
        event_type=event_type,
        outcome=outcome,
        plant_ids=selected_plant_ids,
    )
    existing = completion_records.get(key)
    if isinstance(existing, str) and existing:
        return existing, metadata

    entry_metadata = {
        "source": "task_completion",
        "source_task_id": str(task_row["public_id"]),
        "source_task_type": task_type,
        "outcome": outcome,
        "selected_plant_ids": selected_plant_ids,
    }
    title = ""
    if outcome == "not_seen_blooming_this_season":
        title = "Not seen blooming this season"
    row = db.execute(
        """
        INSERT INTO garden_journal_entries
            (public_id, garden_id, event_type, occurred_on, title, notes,
             metadata_json, actor_user_id, created_at_ms, updated_at_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, public_id
        """,
        (
            generate_public_id("jrn"),
            int(task_row["garden_id"]),
            event_type,
            date.today().isoformat(),
            title,
            notes or "",
            json.dumps(entry_metadata, sort_keys=True, separators=(",", ":")),
            context.user_id,
            now_ms,
            now_ms,
        ),
    ).fetchone()
    assert row is not None
    entry_id = int(row["id"])
    entry_public_id = str(row["public_id"])
    executemany(
        db,
        "INSERT INTO garden_journal_entry_plants (entry_id, plt_id) VALUES (%s, %s)",
        [(entry_id, plant_id) for plant_id in selected_plant_ids],
    )
    executemany(
        db,
        "INSERT INTO garden_journal_entry_plots (entry_id, plot_id) VALUES (%s, %s)",
        [(entry_id, plot_id) for plot_id in selected_plot_ids],
    )
    if task_type == "observe_bloom" and outcome == "done":
        mark_seen_growing_from_observation(
            db,
            garden_id=int(task_row["garden_id"]),
            plant_ids=selected_plant_ids,
            seen_date=date.today().isoformat(),
            plot_ids=selected_plot_ids,
        )
    completion_records[key] = entry_public_id
    if task_type == "observe_bloom" and outcome == "done":
        metadata["completion_journal_entry_id"] = entry_public_id
    return entry_public_id, metadata
```

- [ ] **Step 6: Wire service into task router completion**

In `gardenops/routers/tasks.py`, import:

```python
from gardenops.services.task_completion import (
    CompletionOutcome,
    is_completion_capture_task,
    record_completion_journal_entry,
    validate_completed_plant_ids,
)
```

Remove the old direct calls to `_record_completed_bloom_observation` and `_mark_seen_growing_for_completed_bloom_task` from `_apply_task_action`. In the complete branch:

```python
linked_plant_ids = _task_linked_plant_ids(db, task_id)
selected_plant_ids = validate_completed_plant_ids(
    task_type=str(task_row.get("task_type") or ""),
    linked_plant_ids=linked_plant_ids,
    requested_plant_ids=body.completed_plant_ids,
)
linked_plot_ids = _task_linked_plot_ids(db, task_id)
journal_id, next_metadata = record_completion_journal_entry(
    db,
    context=context,
    task_row=task_row,
    selected_plant_ids=selected_plant_ids,
    selected_plot_ids=linked_plot_ids,
    outcome=body.completion_outcome,
    notes=body.notes,
    now_ms=now_ms,
)
if journal_id is not None:
    db.execute(
        "UPDATE garden_tasks SET metadata_json = %s WHERE id = %s",
        (json.dumps(next_metadata, sort_keys=True, separators=(",", ":")), task_id),
    )
```

Keep the existing task status update for now; partial completion is handled in Task 3.

- [ ] **Step 7: Run tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_tasks.py::TestTasks::test_prune_completion_creates_selected_plant_journal_entry tests/test_tasks.py::TestTasks::test_fertilize_completion_creates_selected_plant_journal_entry tests/test_tasks.py::TestTasks::test_observe_bloom_completion_creates_plant_level_journal_entry tests/test_tasks.py::TestTasks::test_observe_bloom_completion_is_idempotent -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add gardenops/routers/tasks.py gardenops/services/task_completion.py tests/test_tasks.py
git commit -m "feat: record horticultural task completion history"
```

## Task 3: Partial Completion And Notification Refresh

**Files:**
- Modify: `gardenops/services/task_completion.py`
- Modify: `gardenops/routers/tasks.py`
- Modify: `gardenops/services/notification_service.py`
- Test: `tests/test_tasks.py`
- Test: `tests/test_notifications.py`

- [ ] **Step 1: Add failing tests for partial completion and idempotency**

Add to `tests/test_tasks.py`:

```python
def test_grouped_fertilize_partial_completion_keeps_only_remaining_plants(self) -> None:
    response = self.client.post(
        "/api/tasks",
        json={
            "task_type": "fertilize",
            "title": "Fertilize 2 plants",
            "due_on": "2026-06-01",
            "plant_ids": ["PLT-TEST", "PLT-002"],
        },
    )
    self.assertEqual(response.status_code, 201, response.text)
    task_id = response.json()["id"]

    response = self.client.post(
        f"/api/tasks/{task_id}/action",
        json={"action": "complete", "completed_plant_ids": ["PLT-TEST"]},
    )
    self.assertEqual(response.status_code, 200, response.text)

    task = self.client.get(f"/api/tasks/{task_id}").json()
    self.assertEqual(task["status"], "pending")
    self.assertEqual(task["plant_ids"], ["PLT-002"])

    done_journal = self.client.get("/api/journal?event_type=fertilized&plant_id=PLT-TEST").json()
    remaining_journal = self.client.get("/api/journal?event_type=fertilized&plant_id=PLT-002").json()
    self.assertEqual(done_journal["total"], 1)
    self.assertEqual(remaining_journal["total"], 0)


def test_task_completion_capture_is_idempotent_for_same_selected_plants(self) -> None:
    response = self.client.post(
        "/api/tasks",
        json={
            "task_type": "prune",
            "title": "Prune 2 plants",
            "due_on": "2026-06-01",
            "plant_ids": ["PLT-TEST", "PLT-002"],
        },
    )
    self.assertEqual(response.status_code, 201, response.text)
    task_id = response.json()["id"]
    body = {"action": "complete", "completed_plant_ids": ["PLT-TEST"]}

    self.assertEqual(self.client.post(f"/api/tasks/{task_id}/action", json=body).status_code, 200)
    self.assertEqual(self.client.post(f"/api/tasks/{task_id}/action", json=body).status_code, 200)

    journal = self.client.get("/api/journal?event_type=pruned&plant_id=PLT-TEST").json()
    self.assertEqual(journal["total"], 1)
```

- [ ] **Step 2: Add failing notification refresh test**

Add to `tests/test_notifications.py`:

```python
def test_partial_completion_refreshes_task_notification_plant_names(self) -> None:
    from gardenops.services.notification_service import create_task_due_notifications

    response = self.client.post(
        "/api/tasks",
        json={
            "task_type": "fertilize",
            "title": "Fertilize 2 plants",
            "due_on": "2026-06-01",
            "plant_ids": ["PLT-TEST", "PLT-002"],
        },
    )
    self.assertEqual(response.status_code, 201, response.text)
    task_id = response.json()["id"]
    garden_id = self._get_default_garden_id()

    conn = db.get_db()
    try:
        create_task_due_notifications(conn, garden_id)
        conn.commit()
    finally:
        db.return_db(conn)

    response = self.client.post(
        f"/api/tasks/{task_id}/action",
        json={"action": "complete", "completed_plant_ids": ["PLT-TEST"]},
    )
    self.assertEqual(response.status_code, 200, response.text)

    conn = db.get_db()
    try:
        rows = conn.execute(
            """
            SELECT title, metadata_json
            FROM notification_events
            WHERE garden_id = %s
              AND target_type = 'task'
              AND target_id = %s
              AND cleared_at_ms IS NULL
            ORDER BY id ASC
            """,
            (garden_id, task_id),
        ).fetchall()
    finally:
        db.return_db(conn)
    self.assertGreaterEqual(len(rows), 1)
    joined = " ".join(
        f"{row['title']} {json.dumps(row['metadata_json'])}"
        for row in rows
    )
    self.assertNotIn("Test Plant", joined)
    self.assertIn("Rose", joined)
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
uv run pytest tests/test_tasks.py::TestTasks::test_grouped_fertilize_partial_completion_keeps_only_remaining_plants tests/test_tasks.py::TestTasks::test_task_completion_capture_is_idempotent_for_same_selected_plants -q
```

Expected: fail because complete still completes the whole task.

- [ ] **Step 4: Implement remaining-link narrowing**

In `gardenops/services/task_completion.py`, add:

```python
def remaining_plant_ids_after_completion(
    *,
    linked_plant_ids: list[str],
    completed_plant_ids: list[str],
) -> list[str]:
    completed = set(completed_plant_ids)
    return [plant_id for plant_id in linked_plant_ids if plant_id not in completed]


def update_task_plant_links(
    db: DbConn,
    *,
    task_id: int,
    remaining_plant_ids: list[str],
) -> None:
    db.execute("DELETE FROM garden_task_plants WHERE task_id = %s", (task_id,))
    executemany(
        db,
        "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
        [(task_id, plant_id) for plant_id in remaining_plant_ids],
    )
```

Add a title refresh helper that follows current work-order language:

```python
def refreshed_group_title(task_type: str, remaining_names: list[str]) -> str:
    count = len(remaining_names)
    if count == 1:
        prefix = "Prune" if task_type == "prune" else "Fertilize"
        return f"{prefix}: {remaining_names[0]}"
    prefix = "Prune" if task_type == "prune" else "Fertilize"
    return f"{prefix} {count} plants"
```

- [ ] **Step 5: Use partial completion in the router**

In `_apply_task_action`, compute:

```python
remaining_plant_ids = remaining_plant_ids_after_completion(
    linked_plant_ids=linked_plant_ids,
    completed_plant_ids=selected_plant_ids,
)
is_partial_completion = (
    is_completion_capture_task(str(task_row.get("task_type") or ""))
    and selected_plant_ids
    and remaining_plant_ids
)
```

If partial, do not set status completed. Instead:

```python
update_task_plant_links(db, task_id=task_id, remaining_plant_ids=remaining_plant_ids)
db.execute(
    """
    UPDATE garden_tasks
    SET status = 'pending',
        completed_by_user_id = NULL,
        completed_at_ms = NULL,
        snoozed_until = NULL,
        metadata_json = %s,
        updated_at_ms = %s
    WHERE id = %s
    """,
    (json.dumps(next_metadata, sort_keys=True, separators=(",", ":")), now_ms, task_id),
)
```

If not partial, keep the completed update.

- [ ] **Step 6: Add a target-task notification refresh helper**

In `gardenops/services/notification_service.py`, add a helper that refreshes only
the task whose plant set changed. It deliberately does not commit; the task
action endpoint owns the transaction.

```python
def refresh_task_notifications_for_task(
    db: DbConn,
    *,
    garden_id: int,
    task_public_id: str,
    now_ms: int | None = None,
) -> dict[str, int]:
    now_value = now_ms if now_ms is not None else current_timestamp_ms()
    cleared = clear_task_notifications(
        db,
        garden_id=garden_id,
        task_public_id=task_public_id,
        reason="superseded",
        now_ms=now_value,
    )
    # Reuse the existing generator so preference filtering and task_due/task_overdue
    # selection stay consistent. This helper intentionally does not commit.
    result = create_task_due_notifications(db, garden_id)
    return {
        "cleared": cleared,
        "created": int(result.get("created", 0)),
        "skipped": int(result.get("skipped", 0)),
    }
```

If `create_task_due_notifications` commits internally, first split it into:

```python
def _create_task_due_notifications(db: DbConn, garden_id: int) -> dict[str, int]:
    # Move the current create_task_due_notifications body here unchanged, except
    # remove internal db.commit() calls.
    return {"created": created, "skipped": skipped}


def create_task_due_notifications(db: DbConn, garden_id: int) -> dict[str, int]:
    result = _create_task_due_notifications(db, garden_id)
    if int(result.get("created", 0)):
        db.commit()
    return result
```

In the router partial-completion branch, call the non-committing helper:

```python
refresh_task_notifications_for_task(
    db,
    garden_id=int(task_row["garden_id"]),
    task_public_id=str(task_row["public_id"]),
    now_ms=now_ms,
)
```

- [ ] **Step 7: Run backend tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_tasks.py::TestTasks::test_grouped_fertilize_partial_completion_keeps_only_remaining_plants tests/test_tasks.py::TestTasks::test_task_completion_capture_is_idempotent_for_same_selected_plants tests/test_notifications.py::TestRainSuppressedWateringNotificationLifecycle -q
```

Expected: all pass. If the notification test class is not the right home, run the exact new notification test plus the existing stale generated watering lifecycle tests.

- [ ] **Step 8: Commit**

```bash
git add gardenops/routers/tasks.py gardenops/services/task_completion.py gardenops/services/notification_service.py tests/test_tasks.py tests/test_notifications.py
git commit -m "feat: support partial horticultural task completion"
```

## Task 4: Bloom Not-Yet Evidence And Not-Seen Outcome

**Files:**
- Modify: `gardenops/services/task_completion.py`
- Modify: `gardenops/routers/tasks.py`
- Modify: `gardenops/services/task_generator.py`
- Test: `tests/test_tasks.py`
- Test: `tests/test_task_generator_unit.py`

- [ ] **Step 1: Add failing tests for bloom snooze metadata and not-seen outcome**

Add to `tests/test_tasks.py`:

```python
def test_observe_bloom_policy_snooze_records_not_yet_evidence(self) -> None:
    response = self.client.post(
        "/api/tasks",
        json={
            "task_type": "observe_bloom",
            "title": "Observe bloom: Rose",
            "due_on": "2026-06-01",
            "plant_ids": ["PLT-002"],
        },
    )
    self.assertEqual(response.status_code, 201, response.text)
    task_id = response.json()["id"]

    response = self.client.post(
        f"/api/tasks/{task_id}/action",
        json={"action": "snooze", "snooze_until": "2026-06-08"},
    )
    self.assertEqual(response.status_code, 200, response.text)

    task = self.client.get(f"/api/tasks/{task_id}").json()
    events = task["metadata"]["bloom_observation"]["not_yet_events"]
    self.assertEqual(events[0]["new_snooze_date"], "2026-06-08")
    self.assertEqual(events[0]["source"], "task_snooze_policy")


def test_observe_bloom_not_seen_this_season_records_observed_without_presence_change(self) -> None:
    response = self.client.post(
        "/api/tasks",
        json={
            "task_type": "observe_bloom",
            "title": "Observe bloom: Rose",
            "due_on": "2026-06-01",
            "plant_ids": ["PLT-002"],
        },
    )
    self.assertEqual(response.status_code, 201, response.text)
    task_id = response.json()["id"]

    response = self.client.post(
        f"/api/tasks/{task_id}/action",
        json={
            "action": "complete",
            "completion_outcome": "not_seen_blooming_this_season",
        },
    )
    self.assertEqual(response.status_code, 200, response.text)

    journal = self.client.get("/api/journal?event_type=observed&plant_id=PLT-002").json()
    self.assertEqual(journal["total"], 1)
    self.assertEqual(
        journal["entries"][0]["metadata"]["outcome"],
        "not_seen_blooming_this_season",
    )
    plants = {plant["plt_id"]: plant for plant in self.client.get("/api/plants?q=Rose").json()}
    self.assertNotEqual(plants["PLT-002"]["presence_status"], "gone")
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest tests/test_tasks.py::TestTasks::test_observe_bloom_policy_snooze_records_not_yet_evidence tests/test_tasks.py::TestTasks::test_observe_bloom_not_seen_this_season_records_observed_without_presence_change -q
```

Expected: fail because snooze metadata and not-seen outcome are not implemented.

- [ ] **Step 3: Implement bloom not-yet metadata**

In `task_completion.py`, add:

```python
def append_bloom_not_yet_event(
    *,
    task_row: dict[str, Any],
    snooze_until: str,
    actor_user_id: int | None,
    now_ms: int,
) -> dict[str, Any]:
    metadata = parse_task_metadata(task_row)
    bloom = metadata.setdefault("bloom_observation", {})
    events = bloom.setdefault("not_yet_events", [])
    previous_action_date = str(task_row.get("snoozed_until") or task_row.get("due_on") or "")
    events.append(
        {
            "action_at_ms": now_ms,
            "previous_action_date": previous_action_date,
            "new_snooze_date": snooze_until,
            "actor_user_id": actor_user_id,
            "source": "task_snooze_policy",
        }
    )
    return metadata
```

In the router snooze branch, if `task_type == "observe_bloom"`, update metadata with this helper.

- [ ] **Step 4: Ensure not-seen outcome does not mark seen-growing**

The `record_completion_journal_entry` helper must call
`mark_seen_growing_from_observation` only for `task_type == "observe_bloom"` and
`outcome == "done"`. In
`test_observe_bloom_not_seen_this_season_records_observed_without_presence_change`,
add this assertion after loading the plant:

```python
self.assertFalse(plants["PLT-002"]["bloomed_this_year"])
self.assertNotEqual(plants["PLT-002"]["seen_growing"], False)
```

- [ ] **Step 5: Add first-pass generator local-history test**

In `tests/test_task_generator_unit.py`, add a test showing that a plant with a prior `bloomed` journal entry in a later local month does not get a bloom task a full month too early. Keep the first implementation simple:

```python
def test_bloom_generation_prefers_local_observed_month(self) -> None:
    self._insert_plant("BL-LOCAL", "Local Bloomer", bloom_month="juni")
    now_ms = 1_783_180_800_000
    entry = self.conn.execute(
        """
        INSERT INTO garden_journal_entries
            (public_id, garden_id, event_type, occurred_on, title, notes,
             metadata_json, actor_user_id, created_at_ms, updated_at_ms)
        VALUES ('jrn_local_bloom', %s, 'bloomed', '2025-07-15', '', '',
                '{}', %s, %s, %s)
        RETURNING id
        """,
        (self.garden_id, self._owner_id, now_ms, now_ms),
    ).fetchone()
    self.conn.execute(
        "INSERT INTO garden_journal_entry_plants (entry_id, plt_id) VALUES (%s, %s)",
        (int(entry["id"]), "BL-LOCAL"),
    )
    self.conn.commit()

    generate_tasks(self.conn, self.garden_id, 6, 2026, None)
    june_task = self.conn.execute(
        "SELECT * FROM garden_tasks WHERE rule_source = 'bloom_observe:BL-LOCAL:2026-06'",
    ).fetchone()
    assert june_task is None

    generate_tasks(self.conn, self.garden_id, 7, 2026, None)
    july_task = self.conn.execute(
        "SELECT * FROM garden_tasks WHERE rule_source = 'bloom_observe:BL-LOCAL:2026-07'",
    ).fetchone()
    assert july_task is not None
```

- [ ] **Step 6: Implement simple local observed month preference**

In `gardenops/services/task_generator.py`, add a helper that reads prior `bloomed` journal months by plant:

```python
def _local_bloom_months(db: DbConn, garden_id: int) -> dict[str, set[int]]:
    rows = db.execute(
        """
        SELECT jep.plt_id, EXTRACT(MONTH FROM je.occurred_on)::int AS month
        FROM garden_journal_entries je
        JOIN garden_journal_entry_plants jep ON jep.entry_id = je.id
        WHERE je.garden_id = %s
          AND je.event_type = 'bloomed'
        """,
        (garden_id,),
    ).fetchall()
    months_by_plant: dict[str, set[int]] = {}
    for row in rows:
        months_by_plant.setdefault(str(row["plt_id"]), set()).add(int(row["month"]))
    return months_by_plant
```

Inside `generate_tasks`, load once and replace bloom-month matching with:

```python
local_months = local_bloom_months.get(plt_id)
bloom_months = local_months if local_months else _bloom_months(bloom_raw)
if target_month in bloom_months:
    rule = f"bloom_observe:{plt_id}:{target_year}-{target_month:02d}"
    if _rule_exists(db, garden_id, rule):
        skipped += 1
    else:
        desc_en, desc_no = _infer_descriptions_for_rule(
            plant_ctx,
            "bloom_observe",
            target_month,
        )
        task_id = _create_task(
            db,
            garden_id,
            "observe_bloom",
            f"Observe bloom: {name}",
            due_on,
            rule,
            plt_id,
            actor_user_id,
            now_ms,
            description=desc_en,
            metadata_json=_generated_description_metadata(desc_no),
        )
        created_specs.append(
            {
                "task_key": str(task_id),
                "task_id": task_id,
                "task_type": "observe_bloom",
                "due_on": due_on,
                "plant": plant_ctx,
                "fallback_en": desc_en,
                "fallback_no": desc_no,
            }
        )
        created += 1
```

- [ ] **Step 7: Run bloom tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_tasks.py::TestTasks::test_observe_bloom_policy_snooze_records_not_yet_evidence tests/test_tasks.py::TestTasks::test_observe_bloom_not_seen_this_season_records_observed_without_presence_change tests/test_task_generator_unit.py::TestGenerateTasksBloomObservation -q
```

Expected: pass.

- [ ] **Step 8: Commit**

```bash
git add gardenops/routers/tasks.py gardenops/services/task_completion.py gardenops/services/task_generator.py tests/test_tasks.py tests/test_task_generator_unit.py
git commit -m "feat: record bloom timing task outcomes"
```

## Task 5: Frontend Snooze Policy And Toast Actions

**Files:**
- Create: `frontend/src/features/taskSnoozePolicy.ts`
- Modify: `frontend/src/components/toast.ts`
- Modify: `frontend/src/core/appContext.ts`
- Modify: `frontend/src/app.ts`
- Modify: `frontend/src/core/i18n.ts`
- Create: `scripts/check_task_snooze_policy_contract.cjs`
- Modify: `frontend/package.json`

- [ ] **Step 1: Add static contract check that fails before helper exists**

Create `scripts/check_task_snooze_policy_contract.cjs`:

```javascript
#!/usr/bin/env node
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const helper = path.join(root, "frontend/src/features/taskSnoozePolicy.ts");
if (!fs.existsSync(helper)) {
  throw new Error("Missing taskSnoozePolicy.ts");
}
const source = fs.readFileSync(helper, "utf8");
for (const taskType of ["observe_bloom", "prune", "fertilize"]) {
  if (!source.includes(taskType)) {
    throw new Error(`Missing mapped snooze policy for ${taskType}`);
  }
}
if (!source.includes("window_end_on")) {
  throw new Error("Snooze policy must account for window_end_on");
}
```

- [ ] **Step 2: Wire check into build and verify RED**

In `frontend/package.json`, add:

```json
"check:task-snooze-policy": "node ../scripts/check_task_snooze_policy_contract.cjs"
```

Add it before `tsc --noEmit` in `build`.

Run:

```bash
cd frontend && npm run check:task-snooze-policy
```

Expected: fail because helper does not exist.

- [ ] **Step 3: Create snooze policy helper**

Create `frontend/src/features/taskSnoozePolicy.ts`:

```ts
import type { GardenTask, TaskType } from "../core/models";
import { t } from "../core/i18n";

export interface TaskSnoozePolicy {
  defaultDate: string;
  immediate: boolean;
  label: string;
  warning?: string;
}

const ONE_WEEK_TASK_TYPES = new Set<TaskType>([
  "observe_bloom",
  "prune",
  "fertilize",
]);

function addDays(base: Date, days: number): string {
  const next = new Date(base);
  next.setDate(next.getDate() + days);
  return next.toISOString().slice(0, 10);
}

export function taskSnoozePolicy(task: GardenTask, baseDate = new Date()): TaskSnoozePolicy {
  const isWeekDefault = ONE_WEEK_TASK_TYPES.has(task.task_type);
  const defaultDate = addDays(baseDate, isWeekDefault ? 7 : 1);
  const label = task.task_type === "observe_bloom"
    ? String(t("tasks.snooze_check_again_week"))
    : isWeekDefault
      ? String(t("tasks.snooze_one_week"))
      : String(t("tasks.action_snooze"));
  const exceedsWindow = Boolean(
    task.window_end_on
      && (task.task_type === "prune" || task.task_type === "fertilize")
      && defaultDate > task.window_end_on,
  );
  return {
    defaultDate,
    immediate: !exceedsWindow,
    label,
    warning: exceedsWindow ? String(t("tasks.snooze_window_warning")) : undefined,
  };
}
```

Ensure `GardenTask` exposes `window_end_on`; if the model already has it, no type change is needed.

- [ ] **Step 4: Add actionable toast support**

Update `frontend/src/components/toast.ts`:

```ts
type ToastType = "success" | "error";

export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface ToastOptions {
  actions?: ToastAction[];
  durationMs?: number;
}

export function showToast(
  message: string,
  type: ToastType = "success",
  options: ToastOptions = {},
): void {
  const container = getOrCreateContainer();
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.setAttribute("role", "status");
  const text = document.createElement("span");
  text.textContent = message;
  toast.appendChild(text);
  for (const action of options.actions ?? []) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "toast-action";
    button.textContent = action.label;
    button.addEventListener("click", () => {
      action.onClick();
      toast.remove();
    });
    toast.appendChild(button);
  }
  container.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add("toast-visible"));
  const timeout = window.setTimeout(() => {
    toast.classList.remove("toast-visible");
    toast.addEventListener("transitionend", () => toast.remove());
    window.setTimeout(() => toast.remove(), 500);
  }, options.durationMs ?? 5000);
  toast.addEventListener("mouseenter", () => window.clearTimeout(timeout), { once: true });
  toast.addEventListener("focusin", () => window.clearTimeout(timeout), { once: true });
}
```

Update `AppContext.showToast` type to accept the optional options object and pass it through in `frontend/src/app.ts`.

- [ ] **Step 5: Add i18n keys**

Add English/Norwegian keys:

```ts
"tasks.snooze_check_again_week": "Check again in 1 week",
"tasks.snooze_one_week": "Snooze 1 week",
"tasks.snooze_change_date": "Change date",
"tasks.snooze_window_warning": "This would move the task beyond the recommended care window.",
"tasks.snoozed_until_toast": ({ date }) => `Task moved to ${date}`,
```

Use natural Norwegian equivalents in the Norwegian block.

- [ ] **Step 6: Verify frontend helper**

Run:

```bash
cd frontend && npm run check:task-snooze-policy && npm run typecheck
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/taskSnoozePolicy.ts frontend/src/components/toast.ts frontend/src/core/appContext.ts frontend/src/app.ts frontend/src/core/i18n.ts scripts/check_task_snooze_policy_contract.cjs frontend/package.json
git commit -m "feat: add shared task snooze policy"
```

## Task 6: Frontend Completion Flow And Surface Wiring

**Files:**
- Create: `frontend/src/features/taskCompletionFlow.ts`
- Modify: `frontend/src/tabs/tasksTab.ts`
- Modify: `frontend/src/tabs/calendarTab.ts`
- Modify: `frontend/src/components/plotInteractions.ts`
- Modify: `frontend/src/features/quickActionsFeature.ts`
- Modify: `frontend/src/components/tasks.ts`
- Modify: `frontend/src/services/api.ts`
- Modify: `frontend/src/core/i18n.ts`

- [ ] **Step 1: Extend API request type**

In `frontend/src/services/api.ts`:

```ts
export interface TaskActionRequest {
  action: "complete" | "skip" | "snooze" | "reschedule";
  snooze_until?: string;
  reschedule_to?: string;
  notes?: string;
  completed_plant_ids?: string[];
  completion_outcome?: "done" | "not_seen_blooming_this_season";
}
```

- [ ] **Step 2: Create completion-flow helper**

Create `frontend/src/features/taskCompletionFlow.ts`:

```ts
import type { GardenTask } from "../core/models";
import { t } from "../core/i18n";
import { createModal } from "../components/dialogCore";
import type { TaskActionRequest } from "../services/api";

const CAPTURE_TASK_TYPES = new Set(["observe_bloom", "prune", "fertilize"]);

export function needsCompletionSelection(task: GardenTask): boolean {
  return CAPTURE_TASK_TYPES.has(task.task_type) && (task.plant_ids?.length ?? 0) > 1;
}

export function defaultSelectedPlantIds(task: GardenTask): Set<string> {
  const ids = task.plant_ids ?? [];
  return new Set(ids.length <= 5 ? ids : []);
}

export function openTaskCompletionDialog(
  task: GardenTask,
  plantNames: Map<string, string>,
  onConfirm: (body: TaskActionRequest) => void,
): void {
  const selected = defaultSelectedPlantIds(task);
  const { dialog, close } = createModal(String(t("tasks.complete_select_plants_title")), `
    <div class="modal-content task-completion-dialog">
      <h3></h3>
      <div class="task-completion-list"></div>
      <div class="button-row">
        <button type="button" class="task-completion-select-all"></button>
        <button type="button" class="task-completion-clear"></button>
        <button type="button" class="confirm-yes"></button>
        <button type="button" class="confirm-no"></button>
      </div>
    </div>
  `);
  dialog.querySelector("h3")!.textContent = String(t("tasks.complete_select_plants_title"));
  const list = dialog.querySelector<HTMLElement>(".task-completion-list")!;
  for (const plantId of task.plant_ids ?? []) {
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = plantId;
    checkbox.checked = selected.has(plantId);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) selected.add(plantId);
      else selected.delete(plantId);
    });
    label.append(checkbox, document.createTextNode(plantNames.get(plantId) ?? plantId));
    list.appendChild(label);
  }
  dialog.querySelector<HTMLButtonElement>(".task-completion-select-all")!.textContent = String(t("common.select_all"));
  dialog.querySelector<HTMLButtonElement>(".task-completion-clear")!.textContent = String(t("common.clear"));
  dialog.querySelector<HTMLButtonElement>(".confirm-no")!.textContent = String(t("common.cancel"));
  dialog.querySelector<HTMLButtonElement>(".confirm-no")!.addEventListener("click", close);
  const confirm = dialog.querySelector<HTMLButtonElement>(".confirm-yes")!;
  confirm.textContent = String(t("tasks.action_complete"));
  confirm.addEventListener("click", () => {
    const completed_plant_ids = [...selected];
    if (completed_plant_ids.length === 0) return;
    onConfirm({ action: "complete", completed_plant_ids, completion_outcome: "done" });
    close();
  });
}
```

- [ ] **Step 3: Wire Tasks tab complete and snooze**

In `tasksTab.ts`:

- Replace `openSnoozeDialog` default `+1 day` with `taskSnoozePolicy`.
- If policy `immediate` is true, call `handleTaskAction(task.id, "snooze", { snooze_until: policy.defaultDate })`, then show a toast with `Change date`.
- If policy `immediate` is false, open `openDateDialog` with `policy.warning`.
- In `onComplete`, call `openTaskCompletionDialog` when `needsCompletionSelection(task)` is true; otherwise call `handleTaskAction(task.id, "complete")`.
- For batch complete, split selected tasks:
  - tasks needing selection are not sent to `/tasks/batch-action`;
  - show a toast asking the user to complete grouped tasks one by one.

- [ ] **Step 4: Wire Calendar, plot drawer, and quick actions**

Apply the same policy:

- `calendarTab.ts`: use `taskSnoozePolicy` for task detail snooze; use selection dialog for grouped complete if calendar event has plant IDs, otherwise fall back to direct complete.
- `plotInteractions.ts`: replace inline `+7` logic with `taskSnoozePolicy`; keep existing drawer toast but use shared i18n.
- `quickActionsFeature.ts`: replace `+1 day` with `taskSnoozePolicy` for the selected task.

- [ ] **Step 5: Add i18n and minimal styles**

Add keys:

```ts
"tasks.complete_select_plants_title": "Which plants did you actually do?",
"tasks.complete_grouped_one_by_one": "Grouped tasks need plant selection.",
"common.select_all": "Select all",
"common.clear": "Clear",
```

Add CSS for `.task-completion-dialog` and `.toast-action` in `frontend/src/style.css`.

- [ ] **Step 6: Run frontend checks**

Run:

```bash
cd frontend && npm run check:task-snooze-policy && npm run typecheck
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/taskCompletionFlow.ts frontend/src/tabs/tasksTab.ts frontend/src/tabs/calendarTab.ts frontend/src/components/plotInteractions.ts frontend/src/features/quickActionsFeature.ts frontend/src/components/tasks.ts frontend/src/services/api.ts frontend/src/core/i18n.ts frontend/src/style.css
git commit -m "feat: add horticultural task completion flow"
```

## Task 7: Full-Stack Playwright Journey

**Files:**
- Create: `scripts/seed_task_completion_history_e2e.py`
- Create: `scripts/check_task_completion_history_e2e.cjs`
- Create: `scripts/run_task_completion_history_e2e.sh`
- Modify: `frontend/package.json`
- Modify: `docs/development.md`

- [ ] **Step 1: Create E2E seed script**

Create `scripts/seed_task_completion_history_e2e.py` with this structure:

```python
#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys

from gardenops.db import close_pool, get_db, return_db, run_migrations
from gardenops.security import create_user
from gardenops.services.attention import require_attention_e2e_database

E2E_GARDEN_SLUG = "task-history-e2e"
E2E_USERNAME = "task_history_e2e_admin"


def truncate_public_tables(conn) -> None:
    rows = conn.execute(
        """
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename <> 'schema_migrations'
        ORDER BY tablename
        """,
    ).fetchall()
    table_names = [str(row["tablename"]) for row in rows]
    if table_names:
        conn.execute(
            "TRUNCATE TABLE "
            + ", ".join(f'public."{name}"' for name in table_names)
            + " RESTART IDENTITY CASCADE"
        )


def seed(conn) -> None:
    user_id = int(
        create_user(
            conn,
            username=E2E_USERNAME,
            password="TaskHistoryE2E!Passphrase1234567890",  # push-sanitizer: allow SECRET_ASSIGNMENT
            role="admin",
        )["id"]
    )
    garden_id = int(
        conn.execute(
            """
            INSERT INTO gardens (slug, name, grid_rows, grid_cols, onboarding_complete, owner_user_id)
            VALUES (%s, 'Task History E2E', 8, 8, 1, %s)
            RETURNING id
            """,
            (E2E_GARDEN_SLUG, user_id),
        ).fetchone()["id"]
    )
    conn.execute(
        "INSERT INTO garden_memberships (garden_id, user_id, role) VALUES (%s, %s, 'admin')",
        (garden_id, user_id),
    )
    conn.execute(
        """
        INSERT INTO plots (plot_id, garden_id, zone_code, zone_name, plot_number, grid_row, grid_col, sub_zone, notes, color)
        VALUES ('E2E-BED', %s, 'E', 'E2E Bed', 1, 1, 1, '', '', '#7fb069')
        """,
        (garden_id,),
    )
    conn.execute(
        "INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id) VALUES ('E2E-BED', %s, %s)",
        (user_id, garden_id),
    )
    for plant_id, name in [
        ("BLOOM-E2E", "Bloom E2E"),
        ("FERT-A-E2E", "Fertilize A"),
        ("FERT-B-E2E", "Fertilize B"),
        ("PRUNE-A-E2E", "Prune A"),
        ("PRUNE-B-E2E", "Prune B"),
    ]:
        conn.execute(
            """
            INSERT INTO plants (plt_id, name, latin, category, bloom_month, color, hardiness,
                                height_cm, light, link, care_watering, care_soil,
                                care_planting, care_maintenance, care_notes)
            VALUES (%s, %s, '', 'stauder', 'juni', '', '', NULL, '', '', '', '', '', '', '')
            """,
            (plant_id, name),
        )
        conn.execute(
            "INSERT INTO plant_ownership (plt_id, owner_user_id, garden_id) VALUES (%s, %s, %s)",
            (plant_id, user_id, garden_id),
        )
        conn.execute(
            "INSERT INTO plot_plants (plot_id, plt_id, quantity) VALUES ('E2E-BED', %s, 1)",
            (plant_id,),
        )
    now_ms = 1_783_180_800_000
    task_rows = [
        ("tsk_e2e_bloom", "observe_bloom", "Observe bloom: Bloom E2E", "2026-07-05", None, None, ["BLOOM-E2E"]),
        ("tsk_e2e_fertilize", "fertilize", "Fertilize 2 plants", "2026-07-05", None, None, ["FERT-A-E2E", "FERT-B-E2E"]),
        ("tsk_e2e_prune", "prune", "Prune 2 plants", "2026-07-05", "2026-07-05", "2026-07-06", ["PRUNE-A-E2E", "PRUNE-B-E2E"]),
    ]
    for public_id, task_type, title, due_on, window_start, window_end, plant_ids in task_rows:
        task_id = int(
            conn.execute(
                """
                INSERT INTO garden_tasks
                    (public_id, garden_id, task_type, title, description, status, severity,
                     due_on, window_start_on, window_end_on, metadata_json,
                     created_by_user_id, created_at_ms, updated_at_ms)
                VALUES (%s, %s, %s, %s, '', 'pending', 'normal',
                        %s, %s, %s, '{}', %s, %s, %s)
                RETURNING id
                """,
                (public_id, garden_id, task_type, title, due_on, window_start, window_end, user_id, now_ms, now_ms),
            ).fetchone()["id"]
        )
        for plant_id in plant_ids:
            conn.execute(
                "INSERT INTO garden_task_plants (task_id, plt_id) VALUES (%s, %s)",
                (task_id, plant_id),
            )
    conn.commit()


def snapshot(conn) -> None:
    rows = conn.execute(
        """
        SELECT t.public_id, t.status, array_agg(gtp.plt_id ORDER BY gtp.plt_id) AS plant_ids
        FROM garden_tasks t
        LEFT JOIN garden_task_plants gtp ON gtp.task_id = t.id
        GROUP BY t.id
        ORDER BY t.public_id
        """
    ).fetchall()
    journal = conn.execute(
        """
        SELECT je.event_type, je.metadata_json, array_agg(jep.plt_id ORDER BY jep.plt_id) AS plant_ids
        FROM garden_journal_entries je
        LEFT JOIN garden_journal_entry_plants jep ON jep.entry_id = je.id
        GROUP BY je.id
        ORDER BY je.id
        """
    ).fetchall()
    print(json.dumps({"tasks": [dict(row) for row in rows], "journal": [dict(row) for row in journal]}, default=str))


def main() -> None:
    require_attention_e2e_database()
    conn = get_db()
    try:
        run_migrations()
        if len(sys.argv) > 1 and sys.argv[1] == "snapshot":
            snapshot(conn)
            return
        truncate_public_tables(conn)
        seed(conn)
    finally:
        return_db(conn)
        close_pool()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Create Playwright check script**

Create `scripts/check_task_completion_history_e2e.cjs` using `playwright-core`. It must:

- reject route mocks by scanning its own source for `page.route`;
- open Tasks view;
- click bloom snooze and assert the task moves one week;
- click `Change date` and assert the date picker appears;
- complete grouped fertilize selecting only one plant;
- call a Python snapshot subcommand to assert:
  - one `fertilized` journal entry for selected plant;
  - task still pending with only the remaining plant;
- keyboard-open completion dialog and confirm focus/checkbox operation.

- [ ] **Step 3: Create runner script and package command**

Create `scripts/run_task_completion_history_e2e.sh` following the Attention runner shape. Add package script:

```json
"test:task-completion-history-e2e": "cd .. && scripts/run_task_completion_history_e2e.sh"
```

- [ ] **Step 4: Document command**

In `docs/development.md`, add:

```markdown
GARDENOPS_TASK_HISTORY_E2E_TEST_URL="postgresql://localhost/gardenops_task_history_e2e_test" npm run test:task-completion-history-e2e
```

Note that it uses a disposable database only.

- [ ] **Step 5: Run E2E to verify GREEN**

Run from `frontend/` with a disposable database URL:

```bash
GARDENOPS_TASK_HISTORY_E2E_TEST_URL="postgresql://localhost/gardenops_task_history_e2e_test" npm run test:task-completion-history-e2e
```

Expected: pass. If local Postgres roles require it, rerun with:

```bash
GARDENOPS_TASK_HISTORY_E2E_RUN_DB_AS_POSTGRES=1 GARDENOPS_TASK_HISTORY_E2E_TEST_URL="postgresql://localhost/gardenops_task_history_e2e_test" npm run test:task-completion-history-e2e
```

- [ ] **Step 6: Commit**

```bash
git add scripts/seed_task_completion_history_e2e.py scripts/check_task_completion_history_e2e.cjs scripts/run_task_completion_history_e2e.sh frontend/package.json docs/development.md
git commit -m "test: add task completion history e2e journey"
```

## Task 8: Final Verification And PR Prep

**Files:**
- Modify docs only if docs impact inventory flags additional public surfaces.

- [ ] **Step 1: Run focused backend tests**

```bash
uv run pytest tests/test_tasks.py tests/test_notifications.py tests/test_task_generator.py tests/test_task_generator_unit.py -q
```

Expected: pass.

- [ ] **Step 2: Run frontend build**

```bash
cd frontend && npm run build
```

Expected: build completes, including the new task snooze policy contract.

- [ ] **Step 3: Run Playwright E2E**

```bash
cd frontend && GARDENOPS_TASK_HISTORY_E2E_TEST_URL="postgresql://localhost/gardenops_task_history_e2e_test" npm run test:task-completion-history-e2e
```

Expected: pass.

- [ ] **Step 4: Run docs impact inventory**

```bash
python3 .codex/skills/gardenops-documentation-upkeep/scripts/docs_impact_inventory.py --base origin/main
```

Expected: review output and update docs if behavior docs are flagged.

- [ ] **Step 5: Run git hygiene and sanitizer**

```bash
git diff --check
python3 .codex/skills/gardenops-git-push-sanitizer/scripts/git_push_sanitizer.py --pre-push
```

Expected: no blocking findings.

- [ ] **Step 6: Final status**

```bash
git status --short --branch
git log --oneline -8
```

Expected: branch contains the implementation commits and no unintended untracked files.

## Self-Review

- Spec coverage: snooze policy, grouped completion, partial completion, completion history, bloom not-yet evidence, not-seen bloom outcome, notification refresh, offline/batch guardrails, and Playwright are all assigned to tasks.
- Marker scan: this plan contains no deferred work markers and no ellipsis code blocks.
- Type consistency: backend uses `completed_plant_ids` and `completion_outcome`; frontend uses the same names in `TaskActionRequest`; outcome values are `done` and `not_seen_blooming_this_season`.
