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
- Make the panel accessible by keyboard, screen reader, touch, and narrow
  viewport users.
- Keep the first implementation understandable enough that users can predict why
  an item appeared, disappeared, moved, or stayed quiet.

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
Today panel through it. Existing notifications remain in place. Phase 1 must be
read-only with respect to existing `notification_events`: Attention may read and
adapt legacy notifications, but must not clear, supersede, or rewrite legacy
notification rows. New writes are limited to Attention-owned tables for
preferences, user item state, and persisted automation explanations. Later
phases can make notification inbox and email digest consume the same attention
model, then migrate notification mutation ownership intentionally.

The full customization slice keeps that ownership boundary: notification inbox
and email digest delivery may consult Attention preference eligibility, but the
existing notification tables remain the durable inbox/log source and filtered
delivery must not clear, supersede, rewrite, or delete notification rows.

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
audience_user_id
plant_ids
plot_ids
due_on
valid_from
valid_until
domain_state
user_state
lifecycle_scope
delivery_eligibility
rank
group_key
primary_action
secondary_actions
explanation
source_label
updated_at_ms
metadata
```

Categories:

- `needs_action`: the user should do something now.
- `warning`: a current risk or condition the user should understand.
- `upcoming`: near-future work worth previewing.
- `no_action_needed`: something that would have required action, but no longer
  does because context changed or automation handled it.
- `system`: operational or account-level notices.

Domain states:

- `active`
- `completed`
- `skipped`
- `dismissed`
- `expired`
- `superseded`
- `no_action_needed`

User states:

- `unread`
- `read`
- `dismissed`
- `snoozed`
- `preference_hidden`

Lifecycle scope:

- `domain`: a real garden state or automation outcome that applies to the
  garden, such as task completed, weather expired, or watering covered by rain.
- `user`: one user's relationship to the item, such as read, dismissed,
  snoozed, or hidden by that user's preferences.

The lifecycle vocabulary must be strict. Providers can map existing domain state
into these values, but UI surfaces should not invent additional lifecycle terms.
The API response should include the effective `domain_state` and `user_state`
separately so frontend code does not have to infer scope.

Interruption levels:

- `panel_only`: visible in the Map Today panel, not delivered to inbox or email.
- `inbox`: visible in the Map panel and notification inbox.
- `digest`: eligible for email digest, subject to quiet hours and digest cadence.
- `interruptive`: eligible for immediate delivery if a future push/channel
  supports it.

The default should be conservative: most garden work is `panel_only`, routine
due/overdue work is `inbox`, weather or issue risk can become `digest`, and only
high/critical safety or system states may become `interruptive`.

## Providers

Initial providers:

- Task provider: due today, overdue, snoozed-now-active, upcoming, completed,
  skipped, rescheduled, superseded.
- Weather provider: frost, heat, dry spell, rain surplus, weather-linked plant
  advice, weather validity windows.
- Issue provider: open severe issues, overdue follow-ups, recently resolved or
  dismissed follow-ups.
- Calendar provider: manual or generated events that are relevant today or in
  the near future and are not duplicates of task/weather/issue items.
- Notification/status provider: existing system/status notifications that do
  not belong to task, weather, or issue domains.

Badge counts are not an independent provider. Badge surfaces should eventually
derive from the same Attention service or from the same domain queries so counts
do not drift from the Map Today panel.

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

The UI should start with presets and expose advanced controls after that:

- `Calm`: Map panel only for routine items, inbox/email only for high-severity
  weather, issue, or system items.
- `Balanced`: Map panel for routine items, inbox for due/overdue work, email
  digest for normal-or-higher weather and issue items.
- `Detailed`: Map panel and inbox for most items, digest for selected
  categories, including upcoming work.

Default preset rules:

| Category | Calm | Balanced | Detailed |
|---|---|---|---|
| Routine tasks due today | Panel only | Panel + inbox | Panel + inbox + digest |
| Overdue tasks | Panel + inbox | Panel + inbox | Panel + inbox + digest |
| Issue follow-ups | Panel + inbox for high+ | Panel + inbox for normal+ | Panel + inbox + digest for normal+ |
| Weather warnings | Panel + inbox for high+ | Panel + inbox + digest for normal+ | Panel + inbox + digest for low+ |
| Upcoming work | Hidden from panel by default | Panel only for high-priority upcoming items | Panel + inbox |
| No action needed | Collapsed panel history | Collapsed panel history | Expanded panel history |
| System/security | Panel + inbox for all | Panel + inbox for all | Panel + inbox + digest for all |

`Balanced` is the default for new users. Existing users are migrated into a
custom preset derived from their current notification preferences: disabled
notification categories stay disabled for inbox/email, email remains disabled
unless it was already enabled, and the Map panel still shows active attention
unless the user later hides that category from the panel.

Advanced controls should remain category-based and readable. Users should not
have to understand provider names, database types, or rule sources to configure
attention.

Conflict resolution:

1. Domain state wins first. Completed, skipped, expired, superseded, dismissed,
   and no-action-needed domain items cannot remain active just because a user
   preference allows them.
2. User state applies second. A user dismissal or snooze hides or defers that
   user's item without changing the domain record.
3. Guardrails apply third. High/critical safety, frost, security, and system
   items must remain visible in at least one non-email surface.
4. Quiet hours apply only to interruptive or email delivery, not to Map panel
   visibility.
5. Channel eligibility applies last. A category hidden from inbox can still
   appear in the Map panel if panel visibility is enabled.

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

Preference guardrails:

- High/critical safety, frost, security, and system items may be muted from
  interruptive delivery, but must remain available in at least one non-email
  surface such as the Map panel, inbox, or log.
- The app must show where a muted category still appears.
- Preference changes must not delete history; they can hide active items from a
  surface and record the reason as preference-hidden.

## Map Today Panel

The Map remains the first screen.

Desktop behavior:

- The Today panel is open but compact by default.
- It appears as a constrained side panel or overlay that does not hide the main
  map interaction area.
- It shows a small count and the top-ranked active items.
- It includes a collapsed "No action needed" row.
- It provides direct actions or navigation where safe.
- It remembers user collapse/expand state per device only after the user changes
  it. The default remains open and compact on desktop.

Mobile behavior:

- The panel starts as a fixed bottom handle button with the accessible name
  "Today, N items need attention".
- Tapping or pressing Enter/Space on the handle opens a bottom sheet capped at
  60 percent of viewport height.
- Phase 1 should not require drag gestures. This avoids gesture conflict with
  map pan/zoom and makes Playwright coverage deterministic.
- The sheet has an explicit Close button. Closing returns focus to the handle.
- The content is the same feed, with stricter item limits.
- The panel must leave a clear path back to map panning/zooming.
- Touching outside the open sheet returns interaction to the map without
  triggering map movement.

Sections:

- Needs attention.
- Warnings.
- Coming up.
- No action needed, collapsed by default.

Click behavior:

- Selecting any item first applies `map_context` when target metadata is present:
  highlight affected plots, focus affected plants, or select the relevant map
  object without losing the user's current garden context.
- The primary action then opens the relevant workflow: task form/list, issue
  form/list, care/weather view, plant details, plot details, or attention detail.
- Items can also expose a secondary "show on map" action when opening the
  workflow would otherwise leave the Map tab.
- No action needed items open an explanation detail with source, timestamp,
  linked weather/task records, and safe recovery actions when available.

Panel actions:

- Phase 1 quick actions are limited to user-scoped dismiss, user-scoped snooze,
  open details, and safe no-action-needed recovery.
- Domain mutations such as complete task, skip task, resolve issue, or dismiss a
  weather alert remain owned by existing domain workflows in phase 1. The panel
  can deep-link into those workflows and refresh after the workflow closes.
- Safe no-action-needed watering recovery should offer "Add watering task
  anyway" when the original generated task was suppressed before creation, or
  "Move watering back" when an existing generated task was rescheduled.
- The panel includes a settings button labeled "Attention settings" that opens
  preset controls and explains where the current item category is visible.

Ease-of-use rules:

- Every item needs a plain-language reason.
- Every item needs one obvious primary action or a clear "open details" path.
- The panel should show at most the top five active items before a "view all"
  affordance.
- If an item is grouped, the title must say what is grouped and why, for example
  "Water 6 thirsty plants" or "3 issue follow-ups are overdue".
- If an item was suppressed, moved, expired, or superseded, the explanation must
  name the cause, not only the lifecycle state.

Accessibility requirements:

- The panel uses a landmark or labelled region with heading "Today".
- The desktop panel toggle and mobile handle expose `aria-expanded` and
  `aria-controls`.
- The panel must be reachable by keyboard and have a stable heading.
- Opening or closing the panel must restore focus predictably.
- The collapsed mobile handle must have an accessible name and state.
- Section counts must not rely on color alone.
- Severity must be conveyed by text or icon label, not only visual color.
- Dynamic updates should use polite status semantics for counts and content
  changes; severe safety alerts may use assertive semantics only if they require
  immediate awareness.
- Panel controls must meet touch target and keyboard focus requirements.
- Touch targets should be at least 44 by 44 CSS pixels unless an existing design
  system standard is stricter.
- Keyboard order is: panel toggle or handle, section controls, visible item
  primary actions, visible item secondary actions, settings, close.
- Reduced-motion users should not receive animated panel transitions that impair
  orientation.
- With `prefers-reduced-motion: reduce`, panel open/close uses no animated
  slide transition.
- Playwright must assert role/name availability for the panel, toggle/handle,
  expandable No action needed section, settings button, and at least one seeded
  item action. If an accessibility checker is added later, it complements but
  does not replace these role/name and keyboard assertions.

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
- Grouping must never hide a high/critical item inside a low-priority group.
- Group expansion must preserve keyboard order and map highlight behavior.

## Weather-Aware Suppression

Rain/watering is the first proof path.

Weather and task automation remain the owners of task generation and
rescheduling. Attention providers consume those domain outcomes, persist the
no-action-needed explanation, and present the result to the user.

Rules should support:

- Suppress generated watering tasks when sufficient rain covers the due window.
- Reschedule pending generated watering tasks when heavy rain changes the plan.
- Derive no-active-watering attention when watering is no longer relevant.
- Emit a `no_action_needed` attention item explaining what happened.
- Preserve an audit/log trail so users can trust that work did not disappear.
- Offer a way to open the related task/weather details from the no-action-needed
  item.
- Keep manual watering tasks unless the user explicitly opts into suppressing
  them. Generated watering tasks can be suppressed by default when the threshold
  is met.
- Avoid suppressing watering for covered/indoor plants unless their location and
  weather exposure make the rule valid.

The first-pass rainfall threshold is centralized as `RAIN_COVERS_WATERING_MM`
with a default of 10 mm expected or observed across the due date and following
24 hours. An active `rain_surplus` alert that covers the due date also satisfies
the rule. Severe weather signals must not be broadly suppressed by this rule.

Default rule shape:

- Generated outdoor watering task due within the rain alert window.
- A generated watering task is a `garden_tasks` row with `task_type = 'water'`
  and a recognized generated `rule_source`: `water:%` for seasonal generated
  watering or `auto:dry_water:%` for weather-generated dry-spell watering.
  Manual watering tasks have an empty, missing, or unrecognized `rule_source`
  and are not suppressed by rain in phase 1.
- Rain total meets or exceeds `RAIN_COVERS_WATERING_MM` or an active
  `rain_surplus` alert covers the due date.
- The plant or plot is not marked indoor/covered.
- The weather alert is active and not dismissed.
- Result: active watering attention is removed from Needs attention, a
  No action needed item explains the rain coverage, and legacy notification rows
  are not mutated by the Attention service in phase 1.

Exposure data rules:

- Indoor plot records and the indoor plants collection are treated as not
  rain-exposed.
- A plant or task with no plot, an indoor plot, or ambiguous exposure is not
  suppressed by rain in phase 1.
- Covered structures such as greenhouses, patios, or planters should not affect
  suppression until the implementation has a reliable plant/task-to-covered-area
  relationship. Do not infer cover from nearby map objects.

## API

Add:

```text
GET /api/attention/today
GET /api/attention/preferences
PUT /api/attention/preferences
POST /api/attention/items/{item_id}/read
POST /api/attention/items/{item_id}/dismiss
POST /api/attention/items/{item_id}/snooze
POST /api/attention/items/{item_id}/restore
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
      "key": "warnings",
      "title": "Warnings",
      "items": []
    },
    {
      "key": "coming_up",
      "title": "Coming up",
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
  },
  "degraded_providers": [],
  "limits": {
    "active_item_limit": 5,
    "no_action_needed_limit": 5
  }
}
```

The API should enforce active garden scoping, authorization, bounded limits, and
stable ordering.

Action model:

- `primary_action` should be a typed command such as `open_task`,
  `open_issue`, `open_weather`, `focus_plant`, `select_plot`, or
  `open_attention_detail`.
- The frontend must not infer backend mutations from title text.
- User-state mutations such as read, dismiss, and snooze use Attention
  endpoints.
- Preference changes use Attention preference endpoints.
- Safe no-action-needed recovery uses `restore` only when the item exposes that
  supported action. Unsupported restore attempts must return 409.
- `restore` is an orchestrating Attention endpoint: it validates the attention
  outcome and delegates domain changes to existing task/weather services. It
  must not write task, issue, weather, or notification tables directly.
- Domain mutations such as complete task, skip task, resolve issue, or dismiss
  weather alert continue to use existing task, issue, and weather endpoints in
  phase 1.
- Mutating Attention endpoints should return the updated attention item,
  updated section counts, or a refresh hint.

API constraints:

- Response payloads must be bounded by section limits.
- Provider failures must be represented in `degraded_providers` without leaking
  stack traces or secrets.
- Items must include stable ids so Playwright can assert behavior without
  depending on translated display text.
- The response should include enough target metadata for map highlight and
  navigation without extra round trips for the common case.

Compatibility constraints:

- Existing Today/statistics, notification badge, calendar, and weather surfaces
  remain available in phase 1.
- When the Map Today panel and an existing surface count the same domain record,
  they must use the same source filters or the spec/implementation plan must
  explicitly document the temporary difference.
- Any temporary count mismatch needs a backend test or Playwright assertion that
  captures the intended phase-1 behavior so it is not mistaken for drift.

## Storage And Migration

The first pass should derive active attention from existing domain records, but
must persist user-specific state and no-action-needed outcomes so explanations
do not disappear after source records change.

New storage is limited to Attention-owned tables:

- `user_attention_preferences`: preset, channel/category overrides, severity
  thresholds, quiet-hour delivery rules, and no-action-needed visibility.
- `user_attention_item_state`: user-scoped read, dismiss, snooze, and
  preference-hidden overrides keyed by stable attention item id.
- `attention_outcomes`: garden-scoped automation outcomes such as watering
  covered by rain, watering rescheduled by rain, or generated work suppressed.
  This table stores source provider, source record ids, explanation, timestamp,
  affected plants/plots, recovery action metadata, and retention metadata.

Existing notification preferences must be adapted deterministically. Do not
delete or replace current notification behavior in the first phase.

Preference migration mapping:

- Existing global in-app disabled means inbox delivery disabled for migrated
  categories, but Map panel visibility remains enabled until the user changes
  Attention settings.
- Existing global email disabled means digest/email delivery disabled.
- Existing task due/overdue toggles map to the matching task inbox categories.
- Existing per-policy weather, issue, task, and system rules map to the closest
  Attention category and retain the current minimum severity.
- Unknown or future legacy rules are preserved in raw metadata and ignored by
  Attention until explicitly supported.

Migration rules:

- Existing notification events remain readable in inbox/log during the first
  phase.
- Existing `clear_reason` values should map into the Attention lifecycle rather
  than being renamed in place.
- Any new attention tables need garden/user scoping and indexes that match
  `/api/attention/today`.
- `attention_outcomes` entries must be retained long enough for the notification
  log and No action needed panel history to explain recent automation. The first
  implementation should use a 30-day retention default unless implementation
  planning finds an existing retention policy to reuse.
- A rollback should be able to disable the Map Today panel and fall back to the
  current notification system without data loss.

## Error Handling

- If one provider fails, return the rest of the feed and include a non-secret
  degraded-state marker for diagnostics.
- Do not show raw exceptions in the UI.
- Provider failures should not create duplicate notifications or tasks.
- Missing optional providers, such as weather or AI, should produce empty
  provider sections, not broken panels.
- If preferences are invalid or missing, fall back to safe defaults.
- If map highlight data is missing, navigation still opens the domain workflow.
- If a mutating action fails, the UI should keep the item visible and show a
  concise error without clearing the user's context.

## Audit And Trust

Users need to trust that automation did not hide real work.

- Every no-action-needed item must have an explanation, source provider, and
  timestamp.
- Weather-based suppression must record enough source data to answer "why did
  this happen?" later.
- The log should distinguish user actions from automation actions.
- Dismiss, snooze, preference-hide, completed, superseded, expired, and
  no-action-needed outcomes should remain inspectable in a log or linked detail.
- Restore or reschedule should be available for safe no-action-needed watering
  outcomes when the underlying task still exists.

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
- Tests that high/critical guardrail items cannot be hidden from every surface.
- Tests that indoor/covered plants are not suppressed by outdoor rain rules.
- Tests for grouped item ranking and high-severity item escape from groups.
- Tests for degraded provider output.
- Tests for `GET/PUT /api/attention/preferences` migration defaults and preset
  conflict resolution.
- Tests for read, dismiss, snooze, and restore endpoints, including unsupported
  restore returning 409.
- Tests that Attention phase 1 does not mutate existing `notification_events`
  when deriving Map Today output.
- Tests that no-action-needed outcomes persist after source task/weather records
  change and expire according to retention policy.

Playwright end-to-end tests:

- Add a first-class Playwright harness rather than relying only on ad hoc
  scripts.
- Seed a garden with deterministic plants, watering tasks, weather alerts,
  calendar entries, notification preferences, and issue follow-ups.
- Freeze the test clock and use a deterministic weather fixture; no E2E test in
  this suite should depend on live weather, network, or current wall-clock date.
- Provide a test-only way to force one Attention provider to return a degraded
  state.
- Add stable `data-testid` hooks for the Today panel, mobile handle, section
  toggles, seeded item ids, grouped item expanders, settings entry, and item
  actions. Tests should not depend on translated visible text except where copy
  itself is under test.
- Verify the Map loads first and the compact Today panel is open on desktop.
- Verify the panel shows seeded task, warning, issue, calendar, and no-action-
  needed sections according to the fixture.
- Verify "No action needed" is collapsed by default.
- Verify expanding "No action needed" shows rain/watering explanations.
- Verify selecting an item navigates to the correct workflow or highlights the
  relevant map target.
- Verify suppressed generated watering is absent from the Map Today active
  section and absent from active notification inbox only when the source task was
  not created or was rescheduled by existing task/weather automation. Attention
  itself must not mutate legacy notification rows in phase 1.
- Verify keyboard access to open, close, expand, collapse, select, and return to
  the map.
- Verify focus restoration after panel close and item navigation.
- Verify mobile collapsed handle behavior at a narrow viewport.
- Verify preference presets change panel/inbox/digest eligibility in visible
  ways. In phase 1, digest is verified through settings text and
  `/api/attention/today` or `/api/attention/preferences` eligibility state, not
  by sending email.
- Verify a provider failure leaves the panel usable and reports a degraded
  provider state without raw errors.
- Verify grouped watering attention can expand and map-highlight affected
  plants or plots.

E2E journey acceptance:

1. Seed an outdoor hydrangea with a generated watering task due today, an indoor
   basil with a manual watering task, a rain surplus weather alert, and an issue
   follow-up.
2. Open the app at desktop size.
3. Confirm the Map is the first visual surface and the Today panel is open but
   compact.
4. Confirm the outdoor generated watering task is absent from Needs attention.
5. Expand No action needed and confirm the rain explanation is present.
6. Confirm the indoor/manual watering task remains actionable.
7. Open the issue follow-up and return to the Map panel without losing context.
8. Open notification inbox and confirm it does not duplicate the suppressed
   watering item as active because the generated source task was suppressed or
   rescheduled before notification creation.
9. At mobile size, confirm the panel starts as a bottom handle button with
   `aria-expanded=false`, opens to a sheet with `aria-expanded=true`, and closes
   back to the handle without blocking map pan/zoom after close.

Existing backend tests for notifications, weather, scheduler automation, and
task generation should remain part of the safety net.

## Implementation Phases

1. Add Attention domain types, provider interfaces, and service tests.
2. Add task, weather, issue, calendar, and notification/status providers using
   existing data.
3. Add `/api/attention/today`.
4. Add Attention-owned preferences, user item state, automation outcome storage,
   and deterministic migration from existing notification preferences.
5. Add Map Today panel consuming only `/api/attention/today`.
6. Add rain/watering no-action-needed explanation path.
7. Add Playwright seeded E2E coverage for the Morning Garden Check journey.
8. Gradually migrate notification inbox and email digest to consume attention
   output where it reduces duplication.

In the first delivery-filtering pass, "consume attention" means using Attention
preference rules and provider delivery eligibility to decide inbox/digest
visibility while preserving existing notification lifecycle storage. Moving
notification creation, read state, or log ownership fully into Attention remains
a later migration.

Phase gates:

- Do not start broad future providers until the Morning Garden Check journey is
  passing in backend tests and Playwright.
- Do not enable email/digest changes from Attention until preference migration
  and guardrail tests pass.
- Do not remove old notification lifecycle behavior until the new attention
  adapters cover equivalent inbox/log behavior.

## Open Decisions For Implementation Planning

- Exact desktop placement of the compact Map Today panel.
- Whether the existing notification preference form is embedded, linked, or
  replaced after the first Attention settings pass.
- Whether an accessibility checker such as axe is added in addition to required
  Playwright role/name, keyboard, focus, reduced-motion, and touch-target
  assertions.
- Exact copy for degraded provider messaging and preset descriptions.
