# Calendar frontend

The vanilla calendar uses `fullcalendar` 7.0.2 and its daygrid, timegrid,
list, interaction, locale and classic-theme entrypoints. Do not update
`@fullcalendar/core` independently or restore the separate v6 plugin packages.
The core is now transitive; `temporal-polyfill` is an explicit peer dependency.
All packages remain subject to the seven-day release-age policy.

The classic theme and skeleton CSS are bundled locally. GardenOps adds stable
`garden-calendar-*` classes through the public v7 hooks, rather than targeting
upstream generated class names. Container resizing is observed by v7; the old
`updateSize()` call is no longer available. Event source/status classes use the
v7 string-valued `className` input. Local date strings and exclusive range ends
remain the server contract.

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
