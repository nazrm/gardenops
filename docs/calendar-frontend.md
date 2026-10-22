# Calendar frontend

The date-only calendar is implemented in `gardenCalendar.ts`, without a calendar
framework or date polyfill. Month, week, and grouped agenda views use local date
arithmetic and `Intl` formatting. Month grids have six Monday-first weeks; week
views show all-day events rather than an unused hourly time grid. Events retain
their exclusive end-date contract, source/status styles, filters, details, task
actions, manual event editing, feeds, preferences, and offline cache behavior.

Dates support arrow keys and Home/End navigation. Events are native buttons.
Busy days expose additional events in a keyboard-accessible expandable row.
The renderer owns only presentation; `calendarTab.ts` continues to own data
loading, garden isolation, permissions, and mutations. Do not add a general
recurrence, timezone, or scheduling engine to this component.

After `npm ci --ignore-scripts` in `frontend`, run `npm run build` there.
Run the focused browser regression from the repository root:

```sh
CHROMIUM_PATH=/path/to/chrome node scripts/test_calendar_browser.mjs
```

Use an existing Chromium installation compatible with playwright-core. The
test starts an ephemeral loopback frontend, intercepts all API requests with
synthetic fixtures, rejects external requests, and never connects to a backend.
It covers month/week/agenda rendering, source styling, date navigation across
the Europe/Oslo autumn DST transition, and responsive hidden-container reveal.
An optional `CALENDAR_TEST_SCREENSHOT` path captures the synthetic month view.
