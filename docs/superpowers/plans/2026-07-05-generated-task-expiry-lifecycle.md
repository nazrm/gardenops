# Generated Task Expiry Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mark stale generated watering tasks as expired through maintenance so old generated care advice no longer remains pending underneath Attention, notifications, or task views.

**Architecture:** Extend the generated-task lifecycle helper introduced in PR #93 so lifecycle policy mutates domain state before attention providers read tasks. The notification maintenance loop becomes the scheduler entry point, task status gains `expired` as a terminal state, and Attention maps recently expired tasks into collapsed no-action history instead of treating old generated advice as open work.

**Tech Stack:** FastAPI, PostgreSQL via the existing DB wrapper, vanilla TypeScript task models, pytest, ruff, existing GardenOps notification scheduler.

---

## File Structure

- Modify `gardenops/services/generated_task_lifecycle.py`
  - Add `expire_stale_generated_tasks(conn, garden_id, today_iso, now_ms)`.
  - Keep rule-source matching centralized for weekly generated watering and weather-generated dry-watering tasks.
  - Add safe metadata annotations when a task is expired.
- Modify `gardenops/services/notification_service.py`
  - Run generated-task expiry at the start of notification maintenance for each garden.
  - Add a `tasks_expired` summary count.
  - Keep stale-notification cleanup as a backstop for pre-maintenance and historical rows.
- Modify `gardenops/services/attention/providers/tasks.py`
  - Keep stale-generated watering query filters as a pre-maintenance safety net.
  - Include recently `expired` tasks in terminal/no-action history.
  - Map `expired` task status to `domain_state = expired`, `type = task_expired`, `reason = Expired`.
- Modify `gardenops/services/attention/preferences.py`
  - Let `expired` domain items reach the Map panel only as no-action history.
  - Add `task_expired` to preset rule maps.
- Modify `gardenops/routers/tasks.py`
  - Add `expired` to the accepted task status contract.
  - Keep active action views scoped to pending/snoozed actionable tasks.
- Modify `frontend/src/core/models.ts`
  - Add `expired` to `TaskStatus`.
- Modify `frontend/src/core/i18n.ts`
  - Add English and Norwegian labels for expired task chips.
- Modify `frontend/src/components/layout.ts`
  - Add `expired` to the task status filter.
- Modify `frontend/src/tabs/calendarTab.ts`
  - Label `expired` task-backed events defensively if they appear in calendar data.
- Modify `frontend/src/style.css`
  - Style expired task chips and calendar events as terminal, non-active work.
- Test in `tests/test_notifications.py`
  - Maintenance expires stale generated watering tasks and leaves manual watering pending.
  - Expired generated watering notifications clear with reason `expired`.
- Test in `tests/test_attention_api.py`
  - Recently expired generated watering appears only in the `no_action_needed` section with `domain_state = expired`.
- Test in `tests/test_scheduler_automation.py`
  - Maintenance summaries include `tasks_expired`.
- Test in `tests/test_tasks.py`
  - `status=expired` task filtering works and expired tasks stay out of active action views.

## Task 1: Failing Backend Lifecycle Tests

- [x] Add `tests/test_notifications.py::TestRainSuppressedWateringNotificationLifecycle::test_maintenance_expires_stale_generated_watering_tasks`.
- [x] Seed three open watering tasks: generated weekly stale, generated dry-water stale, and manual stale.
- [x] Run notification maintenance with `now_ms` on the day after the generated tasks' action dates.
- [x] Expected RED: generated rows remain `pending` because no expiry mutation exists.
- [x] Add `tests/test_scheduler_automation.py::TestRunMaintenanceIncludes::test_run_maintenance_includes_new_keys` assertion for `tasks_expired`.
- [x] Expected RED: summary has no `tasks_expired` key.

## Task 2: Generated Task Expiry Service

- [x] Implement `expire_stale_generated_tasks` in `gardenops/services/generated_task_lifecycle.py`.
- [x] Select tasks where `status IN ('pending', 'snoozed')`, `task_type = 'water'`, rule source is generated watering, and `COALESCE(snoozed_until, due_on) < today_iso`.
- [x] Update each selected task to `status = 'expired'`, clear snooze/completion fields, set `updated_at_ms = now_ms`, and annotate `metadata_json.lifecycle`.
- [x] Call the helper at the start of `_run_notification_maintenance_for_gardens`.
- [x] Add `tasks_expired` to `_empty_maintenance_summary`.
- [x] Run the Task 1 tests and expect GREEN.

## Task 3: Attention And Task Status Contract

- [x] Add `expired` to the backend `TaskStatus` literal in `gardenops/routers/tasks.py`.
- [x] Keep active-bucket stale-generated watering filters in `TaskAttentionProvider` as a backstop for rows not yet processed by maintenance.
- [x] Include recently expired tasks in the terminal bucket.
- [x] Map `expired` to `task_expired`, `reason = Expired`, and `domain_state = expired`.
- [x] Add `tests/test_attention_api.py::TestAttentionTodayApi::test_today_shows_recently_expired_generated_watering_as_no_action_history`.
- [x] Add or adjust `tests/test_tasks.py` coverage so `status=expired` can be listed but active views exclude it.
- [x] Run the new tests and expect GREEN.

## Task 4: Frontend Status Surface

- [x] Add `expired` to `TaskStatus` in `frontend/src/core/models.ts`.
- [x] Add `tasks.status_expired` to English and Norwegian i18n.
- [x] Add an `expired` option to the task status filter in `frontend/src/components/layout.ts`.
- [x] Add a defensive calendar label branch for `expired` in `frontend/src/tabs/calendarTab.ts`.
- [x] Add neutral terminal styling for expired task chips and calendar entries in `frontend/src/style.css`.
- [ ] Run frontend static/build validation after backend tests pass.

## Task 5: Documentation, Verification, And PR Update

- [ ] Run docs impact inventory from the worktree.
- [ ] Update docs if the inventory or behavior review identifies a public docs surface; otherwise record why docs are unchanged in the PR.
- [ ] Run focused backend tests:
  - `tests/test_notifications.py::TestRainSuppressedWateringNotificationLifecycle`
  - `tests/test_attention_api.py::TestAttentionTodayApi`
  - `tests/test_tasks.py`
  - `tests/test_scheduler_automation.py`
  - `tests/test_task_generator.py`
- [ ] Run ruff format/check on touched backend files and tests.
- [ ] Run frontend build/static checks if frontend files changed.
- [ ] Run `git diff --check`.
- [ ] Run push sanitizer pre-add, pre-commit, and pre-push.
- [ ] Amend or add a commit on `codex/stale-generated-task-lifecycle`.
- [ ] Push to update PR #93.
- [ ] Update the PR body so it describes expiry, not only hiding.

## Self-Review

- Spec coverage: watering expiry, Attention no-action mapping, task status contract, notification maintenance, frontend labels, and tests are each covered by a task.
- Placeholder scan: no deferred implementation placeholders.
- Type consistency: task status is `expired`; Attention domain state remains `expired`; no user-state dismissal is introduced.
