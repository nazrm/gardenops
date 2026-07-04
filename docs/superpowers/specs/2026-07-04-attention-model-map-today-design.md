# Attention Model And Map Today Panel Design

Date: 2026-07-04

## Purpose

GardenOps needs a clearer attention model before adding more planner, diagnosis,
weather, or care automation. The current app already has tasks, weather alerts,
issue follow-ups, notification events, calendar entries, badge counts, and
preference rules. Adding more generators directly to notifications would make
the product harder to understand.

This design introduces a modular Attention domain and a compact Today panel on
the Map page. The Map remains the primary first screen. The Today panel explains
what needs attention now, what changed, and which items need no action because
GardenOps handled or suppressed them.

## User Case

Morning Garden Check:

1. The user opens GardenOps before going outside.
2. The Map is still front and center.
3. A compact Today panel is open by default on desktop.
4. The panel shows a short prioritized list of garden attention items.
5. The user sees urgent issue follow-ups, severe weather, overdue tasks, and
   current care actions without opening several tabs.
6. Rain or other context can suppress or reschedule watering work.
7. Suppressed or resolved items are shown behind a collapsed "No action needed"
   row, with clear explanations.
8. Selecting an item opens the correct workflow and, where possible, highlights
   the relevant plot or plant on the map.

Example:

- Needs attention: "Check mildew on cucumber", "Protect basil from heat",
  "Harvest lettuce".
- No action needed: "Watering skipped for hydrangea: 18 mm rain expected",
  "Watering moved to tomorrow after heavy rain".

## Goals

- Preserve Map-first product identity.
- Add a compact Today panel to the Map page without turning GardenOps into a
  generic dashboard.
- Create a provider-based Attention domain for tasks, weather, issues,
  notifications/status, and future modules.
- Separate attention from delivery. Notifications, email, and the Map panel are
  delivery or visibility surfaces, not the source of truth.
- Normalize lifecycle vocabulary across attention-producing systems.
- Give users clear control over what appears where and how it is delivered.
- Make weather-aware suppression explicit and trustworthy.
- Prove the full journey with backend tests and Playwright end-to-end tests.

## Non-Goals

- Do not replace the Map as the default desktop experience.
- Do not rewrite every notification table and preference at once.
- Do not make notifications the new abstraction.
- Do not add new planning or diagnosis generators as part of the first pass.
- Do not hide high-severity safety, frost, security, or system signals with
  broad global suppression.

## Architecture

Introduce a new Attention domain as a strangler layer beside existing systems.

```text
Domain records
  tasks, weather alerts, issues, notifications, future planner signals
        |
        v
Attention providers
  task provider, weather provider, issue provider, status provider, future providers
        |
        v
Attention service
  normalize, rank, group, apply preferences, explain lifecycle
        |
        v
Surfaces
  Map Today panel, notification inbox/log, email digest, future push/mobile
```

The first implementation should create the Attention domain and route the Map
Today panel through it. Existing notifications remain in place. Later phases can
make notification inbox and email digest consume the same attention model.

## Attention Item Contract

Each provider returns bounded attention candidates using a shared contract:

```text
id
provider
type
category
severity
title
body
reason
target_type
target_id
garden_id
plant_ids
plot_ids
due_on
valid_from
valid_until
lifecycle_state
delivery_eligibility
metadata
```

Categories:

- `needs_action`: the user should do something now.
- `warning`: a current risk or condition the user should understand.
- `upcoming`: near-future work worth previewing.
- `no_action_needed`: something that would have required action, but no longer
  does because context changed or automation handled it.
- `system`: operational or account-level notices.

Lifecycle states:

- `active`
- `read`
- `dismissed`
- `snoozed`
- `completed`
- `expired`
- `superseded`
- `no_action_needed`

The lifecycle vocabulary must be strict. Providers can map existing domain state
into these values, but UI surfaces should not invent additional lifecycle terms.

## Providers

Initial providers:

- Task provider: due today, overdue, snoozed-now-active, upcoming, completed,
  skipped, rescheduled, superseded.
- Weather provider: frost, heat, dry spell, rain surplus, weather-linked plant
  advice, weather validity windows.
- Issue provider: open severe issues, overdue follow-ups, recently resolved or
  dismissed follow-ups.
- Notification/status provider: existing system/status notifications that do
  not belong to task, weather, or issue domains.

Future providers:

- Planner guidance.
- Diagnosis follow-ups.
- Procurement readiness.
- Inventory shortages.
- Harvest windows.
- Sensor or irrigation integrations.

Each provider should be small and testable. A future feature should add a
provider or provider rule rather than editing the Map panel directly.

## Preferences And Delivery

Attention preferences should support a delivery matrix. A user can choose both
visibility and delivery per type/category/severity.

Controls should map to user-facing concepts:

- Show in Map Today panel.
- Show in notification inbox.
- Include in email digest.
- Minimum severity.
- Quiet hours for delivery channels that interrupt the user.
- Allow weather-aware automatic suppression for watering reminders.
- Show or hide low-priority "No action needed" history.

Examples:

- Show watering attention in the Map panel, but do not send notifications.
- Email frost warnings at normal severity or above.
- Keep low-priority weather in the Map panel only.
- Collapse "No action needed" by default.
- Let sufficient rain suppress watering reminders.

Preferences are user-scoped. Domain outcomes are garden-scoped when the real
garden state changes. For example, one user dismissing a panel item should not
hide it for every member, but completing a task should remove that action for
everyone.

## Map Today Panel

The Map remains the first screen.

Desktop behavior:

- The Today panel is open but compact by default.
- It appears as a constrained side panel or overlay that does not hide the main
  map interaction area.
- It shows a small count and the top-ranked active items.
- It includes a collapsed "No action needed" row.
- It provides direct actions or navigation where safe.

Mobile behavior:

- The panel starts collapsed to a bottom handle or compact badge.
- Opening the panel should not trap the user away from the map.
- The content is the same feed, with stricter item limits.

Sections:

- Needs attention.
- Coming up, if useful and not noisy.
- No action needed, collapsed by default.

Click behavior:

- Task item: open task workflow.
- Issue item: open issue workflow.
- Weather item: open care/weather workflow.
- Plant item: focus plant.
- Plot item: select or highlight plot on the map.
- No action needed item: show explanation and link to related log/workflow.

## Ranking And Grouping

The feed must be bounded and predictable.

Default ordering:

1. Critical or high severity warnings.
2. Overdue issue follow-ups.
3. Overdue tasks.
4. Tasks due today.
5. Active weather advice.
6. Upcoming work.
7. No action needed, collapsed.

Grouping rules:

- Group repeated watering tasks when the user benefit is a single decision.
- Keep severe or distinct issue/weather items separate.
- Prefer plot/plant summaries over long repeated lists.
- Return enough item metadata for the UI to show "why" without extra API calls.

## Weather-Aware Suppression

Rain/watering is the first proof path.

Rules should support:

- Suppress generated watering tasks when sufficient rain covers the due window.
- Reschedule pending generated watering tasks when heavy rain changes the plan.
- Clear or supersede active watering notifications when watering is no longer
  relevant.
- Emit a `no_action_needed` attention item explaining what happened.
- Preserve an audit/log trail so users can trust that work did not disappear.

The exact rainfall threshold should be centralized and tested. Severe weather
signals must not be broadly suppressed by this rule.

## API

Add:

```text
GET /api/attention/today
```

Response shape:

```json
{
  "generated_at_ms": 1783180800000,
  "garden_id": 1,
  "summary": {
    "needs_attention_count": 3,
    "warning_count": 1,
    "no_action_needed_count": 2
  },
  "sections": [
    {
      "key": "needs_attention",
      "title": "Needs attention",
      "items": []
    },
    {
      "key": "no_action_needed",
      "title": "No action needed",
      "collapsed": true,
      "items": []
    }
  ],
  "preferences": {
    "customizable": true
  }
}
```

The API should enforce active garden scoping, authorization, bounded limits, and
stable ordering.

## Storage And Migration

The first pass should derive most attention from existing domain records.

New storage should be limited to:

- User-specific attention lifecycle overrides when existing domain state is not
  enough.
- Attention preferences that cannot be represented by current notification
  preferences.
- Optional lightweight log records for no-action-needed explanations if current
  notification/task/weather history cannot represent them cleanly.

Existing notification preferences should be adapted into attention preferences
where possible. Do not delete or replace current notification behavior in the
first phase.

## Error Handling

- If one provider fails, return the rest of the feed and include a non-secret
  degraded-state marker for diagnostics.
- Do not show raw exceptions in the UI.
- Provider failures should not create duplicate notifications or tasks.
- Missing optional providers, such as weather or AI, should produce empty
  provider sections, not broken panels.
- If preferences are invalid or missing, fall back to safe defaults.

## Testing

Backend tests:

- Unit tests for provider normalization.
- API tests for `/api/attention/today` authorization, garden scoping, ordering,
  grouping, preference filtering, and bounded output.
- Regression tests for rain suppressing or rescheduling watering attention.
- Regression tests for lifecycle mapping from completed, skipped, snoozed,
  expired, superseded, resolved, and dismissed domain records.
- Tests that one user's dismissed item does not hide the same domain item for
  other garden members.

Playwright end-to-end tests:

- Add a first-class Playwright harness rather than relying only on ad hoc
  scripts.
- Seed a garden with plants, watering tasks, weather alerts, and issue
  follow-ups.
- Verify the Map loads first and the compact Today panel is open on desktop.
- Verify the panel shows broader attention items.
- Verify "No action needed" is collapsed by default.
- Verify expanding "No action needed" shows rain/watering explanations.
- Verify selecting an item navigates to the correct workflow or highlights the
  relevant map target.
- Verify notification inbox remains calmer after attention items are
  auto-cleared or suppressed.

Existing backend tests for notifications, weather, scheduler automation, and
task generation should remain part of the safety net.

## Implementation Phases

1. Add Attention domain types, provider interfaces, and service tests.
2. Add task, weather, issue, and notification/status providers using existing
   data.
3. Add `/api/attention/today`.
4. Add attention preferences adapter and minimal new preference storage only
   where current notification preferences are insufficient.
5. Add Map Today panel consuming only `/api/attention/today`.
6. Add rain/watering no-action-needed explanation path.
7. Add Playwright seeded E2E coverage for the Morning Garden Check journey.
8. Gradually migrate notification inbox and email digest to consume attention
   output where it reduces duplication.

## Open Decisions For Implementation Planning

- Exact rainfall threshold and date window for "rain is enough".
- Whether no-action-needed explanations need their own persistent table in the
  first phase or can be derived from task/weather metadata.
- Exact desktop placement of the compact Map Today panel.
- Whether low-priority upcoming items appear in the panel by default.
- How much of the existing notification preference UI is reused versus replaced
  in the first implementation pass.
