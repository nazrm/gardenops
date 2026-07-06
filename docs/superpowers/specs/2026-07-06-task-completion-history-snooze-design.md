# Task Completion History And Snooze Policy Design

Date: 2026-07-06

## Purpose

GardenOps tasks should behave like prompts to record real garden work, not only
checkboxes that disappear from a list. This matters most for horticultural tasks
such as bloom observation, pruning, fertilizing, watering, harvesting, dividing,
deadheading, and planting out. When the user completes one of these tasks, the
app should preserve what happened as journal history. When the user snoozes one,
the app should use a task-appropriate default instead of forcing repeated date
entry or letting each UI surface make up its own rule.

The immediate user problem is bloom observation. Predicted bloom windows can be
about a month early for the user's location, so generated bloom checks often need
several "not yet, check again" actions. That should be fast, should not feel like
failing the task, and should become evidence for future local timing.

## Current Rule Path

- Generated care tasks are created by `gardenops/services/task_generator.py`.
  Bloom observation tasks use `task_type = 'observe_bloom'` and a
  `bloom_observe:<plant>:<year-month>` rule source.
- Task actions are handled by `gardenops/routers/tasks.py`. The current action
  endpoint supports `complete`, `skip`, `snooze`, and `reschedule`.
- Completing `observe_bloom` already creates a plant-level `bloomed` journal
  entry and updates plant bloom/seen-growing metadata.
- Pruning and fertilizing completion currently complete the task but do not yet
  create durable plant-level journal history.
- Snooze behavior is inconsistent across surfaces:
  - Plot drawer inline snooze uses `+1 week`.
  - Mobile quick action uses `+1 day`.
  - Tasks and Calendar use manual date prompts/dialogs.
- Attention reads task state separately from task actions. Task snooze changes
  the domain task, while Attention snooze only hides an attention item for one
  user. This design concerns domain task snooze.

## User Case

Bloom check journey:

1. The user opens the Map Today panel or Tasks view.
2. GardenOps shows "Observe bloom: Pulmonaria" because the plant's catalog bloom
   month says it may bloom now.
3. The user sees the plant is not blooming yet.
4. The user clicks the task snooze action.
5. GardenOps immediately moves the task one week forward and shows a short
   toast with `Change date` and, where feasible, `Undo`.
6. If the user ignores the toast, the task is no longer in today's actionable
   set.
7. If the user clicks `Change date`, GardenOps opens a date picker and updates
   the same task.
8. Repeated weekly snoozes remain visible to the system as "not yet observed"
   timing evidence.
9. When the user eventually completes the bloom task, GardenOps records a
   `bloomed` journal entry for the selected plant.
10. Future generated bloom checks prefer observed local timing over generic
    catalog bloom months.

Grouped pruning/fertilizing journey:

1. GardenOps generates a grouped task such as "Fertilize 12 perennials".
2. The user completes only seven plants.
3. The completion sheet asks which linked plants were actually done.
4. GardenOps records `fertilized` history for the seven selected plants.
5. The active task remains for only the five remaining plants.
6. The user can snooze the remaining task by one week or choose a manual date.

## Goals

- Make task snooze defaults consistent across Map drawer, Today panel, Tasks,
  Calendar, mobile quick actions, offline drafts, and batch actions.
- Keep task snooze distinct from Attention item snooze.
- Make `observe_bloom`, `prune`, and `fertilize` default to `+1 week`.
- Preserve a manual date path without making it the common path.
- Convert horticultural task completion into durable journal history.
- Let users select which linked plants were actually completed for grouped
  tasks.
- Support partial completion without showing completed plants again in the same
  active grouped task.
- Treat repeated bloom snoozes as local timing evidence.
- Stop rolling bloom checks forward forever after the plausible local bloom
  window has passed.
- Keep the design modular so more task types can adopt policy and completion
  capture later.

## Non-Goals

- Do not redesign the full Journal UI.
- Do not merge task snooze and Attention snooze.
- Do not rewrite the whole task generator.
- Do not add a new `partially_completed` task status.
- Do not require manual date selection for the common "check again next week"
  case.
- Do not infer false plant history for grouped tasks without user selection.
- Do not implement full phenology prediction in the first slice; capture the
  data and use simple local-history preference first.

## Design

### 1. Task Snooze Policy

Create a shared task snooze policy used by every task action surface. The policy
returns the default snooze date, label, and whether the action can run
immediately.

Initial policy:

| Task type | Default | Primary label | Manual date path |
|---|---:|---|---|
| `observe_bloom` | `+7 days` | Check again in 1 week | Change date |
| `prune` | `+7 days` | Snooze 1 week | Change date |
| `fertilize` | `+7 days` | Snooze 1 week | Change date |
| Other single task types | Existing/manual behavior unless explicitly mapped | Snooze | Date picker |
| Mixed batch selection | Manual date | Snooze | Date picker |

Surfaces should call the same helper instead of hard-coding `+1 day`, `+7 days`,
or a prompt. The policy belongs close to task behavior, not inside a specific
component. Backend validation still accepts explicit `snooze_until`; the
frontend policy supplies the default date.

The primary snooze flow should not be an auto-closing modal. It should be a
toast/snackbar after the action succeeds:

- Message: task-specific confirmation such as "Bloom check moved to 2026-07-13".
- Action: `Change date`, opening the normal date picker for the same task.
- Optional action: `Undo`, if the existing action infrastructure can support it
  safely in the implementation slice.
- Timeout: long enough for accessibility, and paused on hover/focus where the
  toast system supports it.

If the task has a `window_end_on` and the default `+7 days` would move prune or
fertilize work beyond the recommended window, the UI should not silently apply
the default. It should open the date picker with the proposed date and a short
window warning. This keeps the default fast for ordinary deferrals without
teaching the app to push time-sensitive horticultural work outside its care
window.

### 2. Completion As History

Introduce an explicit completion-capture layer for horticultural task types.
This layer maps completed task work to journal event types.

Initial mapping:

| Task type | Journal event |
|---|---|
| `observe_bloom` | `bloomed` |
| `prune` | `pruned` |
| `fertilize` | `fertilized` |

Likely future mappings:

| Task type | Journal event |
|---|---|
| `water` | `watered` |
| `harvest` | `harvested` |
| `divide` | `divided` |
| `plant_out` | `planted` |
| `deadhead` | `observed` or a future `deadheaded` event |

The implementation should start with `observe_bloom`, `prune`, and
`fertilize`. Bloom completion already exists and should be refactored into the
shared completion-capture path rather than duplicated.

Journal entries created from task completion should include metadata:

- `source = "task_completion"`
- `source_task_id`
- `source_task_type`
- selected plant IDs
- selected plot IDs where known
- actor user ID through the existing journal actor fields
- completion timestamp/date
- task completion notes when supplied

The event date should default to the action date. A future enhancement can allow
the user to override the occurred date in the completion sheet.

Completion capture must be idempotent. Retrying the same completion request must
not create duplicate journal entries for the same task, event type, outcome, and
selected plant set. Task metadata should store enough completion-capture records
to distinguish full completion, partial completion batches, and the existing
single bloom completion journal entry.

### 3. Grouped Completion Plant Selection

Completing a task linked to more than one plant opens a compact completion sheet
instead of immediately completing the whole task.

The sheet includes:

- Task title and task type.
- Linked plant list with checkboxes.
- `Select all`.
- `Clear`.
- Confirm button.
- Cancel button.

Default selection:

- 1-5 linked plants: selected by default.
- 6 or more linked plants: none selected by default, with `Select all`
  available.

This balances speed for small grouped tasks with accuracy for large groups.

Single-plant tasks may remain one-tap because the target is unambiguous. If the
single plant task has missing plant linkage, completion should fall back to the
existing plain task completion behavior and not create plant-level history.

Batch completion must not blindly complete grouped horticultural tasks that need
plant selection. If a batch contains a multi-plant `observe_bloom`, `prune`, or
`fertilize` task, the frontend should open the completion sheet for that task or
exclude it from the batch with a clear message. The backend should reject
multi-plant completion requests that omit `completed_plant_ids`.

### 4. Partial Completion

Do not add `partially_completed` as a public task status. It makes active views,
attention ranking, filters, and notification cleanup harder to reason about.

Instead:

- If the selected plants are all linked plants, complete the task and create the
  journal history.
- If only some linked plants are selected, create the journal history for the
  selected plants and update the active task to keep only unselected linked
  plants.
- The remaining task keeps its existing task ID. Existing attention and
  notification references should stay stable, while the task-to-plant links and
  metadata change to describe the remaining work.
- The task title and metadata should be refreshed so counts and plant names match
  the remaining targets.
- Existing task notifications should be cleared or superseded when the linked
  plant set changes, then regenerated for the remaining task if it is still
  currently actionable. The user should not be left with a stale notification
  that names completed plants, and the remaining task should not go quiet until
  the next maintenance run if it is still due.

### 5. Bloom Timing Feedback

Snoozing an `observe_bloom` task by policy means "not yet observed", not
"dismissed". Store each policy snooze as a structured entry in task metadata
under `bloom_observation.not_yet_events`, including the action date, previous
action date, new snooze date, actor user ID, and source `task_snooze_policy`.
Future generation can then distinguish bloom-timing evidence from arbitrary
deferral.

Initial local timing rules:

- Prefer current-year observed bloom dates over catalog bloom months.
- If a plant has previous local bloom observations, generate the next bloom
  check near the median/typical local observed date rather than the generic
  month start.
- Repeated "not yet" snoozes push the active check forward by one week without
  creating a bloom history event.
- Once the check is sufficiently beyond the plausible local bloom window, stop
  rolling it forward and offer `Not seen this season`.

First-slice expiry rule:

- A generated bloom observation task may keep rolling weekly while it remains
  within the catalog bloom month plus a local grace window.
- The local grace window should allow at least one month of delay because the
  user's garden can run about one month later than catalog data.
- After that window, show an explicit `Not seen this season` path that records
  an `observed` journal entry with metadata
  `outcome = "not_seen_blooming_this_season"` and resolves the active task as
  `completed`.
- `Not seen this season` for a bloom task means "not observed blooming", not
  "the plant was not seen growing." It must not set plant or plot
  `seen_growing = false`, must not mark the plant gone, and must not erase a
  current-year seen-growing observation from another workflow.

The exact local-history prediction algorithm can remain simple in the first
implementation. The important boundary is data capture: snoozes and completed
bloom observations must be stored in a way that a better phenology model can use
later.

### 6. Attention, Notifications, And Calendar

Task domain state remains the source of truth.

- Task snooze updates `garden_tasks.status = 'snoozed'` and `snoozed_until`.
- Attention snooze remains user-scoped visibility state only.
- Today/Attention should read the updated task state and hide future snoozed
  tasks until they become actionable again.
- Task notifications should clear or supersede according to the existing stale
  task notification rules when a task is snoozed, completed, or narrowed by
  partial completion.
- Calendar should show the updated task date after snooze and the updated plant
  targets after partial completion.

### 7. Offline Behavior

Offline task actions should use the same policy date that online actions use.
Queued drafts should store the explicit `snooze_until` and selected plant IDs for
completion capture. When replayed, backend validation must reject stale or
invalid selections cleanly if task links changed while offline.

For partial completion offline, a safe first implementation can require online
mode for grouped completion if the existing offline draft system cannot preserve
selected plant IDs reliably. Single-task snooze should remain offline-capable.

## API Shape

The existing task action endpoint can be extended without introducing a separate
completion endpoint:

```json
{
  "action": "complete",
  "completed_plant_ids": ["PLT-001", "PLT-002"],
  "completion_outcome": "done"
}
```

Rules:

- `completed_plant_ids` is optional for single-plant tasks.
- For multi-plant horticultural tasks, `completed_plant_ids` is required and
  must be a non-empty subset of the linked task plants.
- `completion_outcome` defaults to `done`.
- `observe_bloom` also accepts `completion_outcome =
  "not_seen_blooming_this_season"` when the UI uses the `Not seen this season`
  path.
- Empty selection is a client-side cancel/no-op, not a backend completion.
- Unknown or unauthorized plant IDs return a validation error.
- Completion history creation and task status/link updates happen in one DB
  transaction.
- Backend validation must reject `completed_plant_ids` on non-horticultural task
  types until those task types have an explicit completion-capture mapping.

The existing snooze request remains:

```json
{
  "action": "snooze",
  "snooze_until": "2026-07-13"
}
```

The policy date is computed before this request is sent.

## Accessibility And UX Requirements

- Completion sheets must be real dialogs/sheets with focus trap, Escape/Cancel,
  visible labels, and keyboard-operable checkboxes.
- The plant list must be usable on mobile and must not rely on hover.
- Toast actions must be reachable by keyboard and screen readers while visible.
- Auto-dismiss timing must not be the only way to access manual date changes;
  manual snooze remains available through the task action surface.
- Button labels should describe the task-specific action where space allows,
  such as `Check again in 1 week` for bloom tasks.

## Testing Requirements

Backend tests:

- Task snooze policy dates are represented consistently by frontend helpers and
  accepted by backend validation.
- Completing `prune` creates a `pruned` journal entry for selected plants.
- Completing `fertilize` creates a `fertilized` journal entry for selected
  plants.
- Existing `observe_bloom` completion still creates one idempotent `bloomed`
  journal entry.
- Multi-plant completion with all plants completes the task.
- Multi-plant completion with a subset logs selected plants and leaves only the
  remaining plants linked to active work.
- Retried completion requests do not duplicate journal entries.
- Invalid selected plant IDs are rejected.
- Repeated bloom snoozes are recorded as "not yet observed" timing evidence.
- Generated bloom checks can transition to `Not seen this season` after the
  local grace window without changing seen-growing presence state.
- Notification cleanup respects snoozed/completed/partially completed tasks.
- Batch completion rejects or separates grouped horticultural tasks that require
  plant selection.

Frontend tests:

- Tasks, Calendar, plot drawer, mobile quick actions, and Today use the same
  snooze default for mapped task types.
- `observe_bloom`, `prune`, and `fertilize` show a `+1 week` path.
- `Change date` opens the manual date picker.
- Prune/fertilize default snooze opens the date picker with a warning when the
  default date exceeds `window_end_on`.
- Grouped completion opens the plant-selection sheet.
- Default checkbox selection is all for 1-5 plants and none for 6+ plants.
- Partial completion updates the visible task targets.
- Batch completion routes grouped horticultural tasks through selection instead
  of silently completing every linked plant.

Playwright end-to-end tests:

- Bloom journey: generated bloom task, snooze one week, change date, complete,
  verify journal entry and task state.
- Grouped prune/fertilize journey: select subset, complete, verify journal
  history and remaining task targets.
- Keyboard journey: open completion sheet, select plants, confirm, and verify
  focus/announcements are usable.

## Rollout

1. Add shared snooze policy and wire it into existing frontend task surfaces.
2. Refactor bloom completion into shared completion-capture service.
3. Add prune/fertilize journal creation on completion.
4. Add grouped completion plant-selection UI and backend selection validation.
5. Add partial completion target narrowing.
6. Add bloom "not yet" timing evidence and first-pass `Not seen this season`
   lifecycle.
7. Add backend, frontend, and Playwright coverage.

## Deferred Scope

- `Undo` in the snooze toast is desirable but optional for the first
  implementation if it requires broad action-history machinery.
- Watering completion-to-journal should be handled after generated watering
  lifecycle rules are settled, because weather can invalidate generated watering
  tasks.
- `deadhead` may need a dedicated journal event before it should become a
  completion-capture task type.
