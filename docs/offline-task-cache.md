# Offline task-list scope

An exact cached query may be reused only for the same garden and normalized query
parameters. A different query can be derived only from a complete, first-page
snapshot with no filters except the same `view`. Plot, status, and task-type
filters run before offset/limit pagination; `total` is the filtered count. Unknown
query parameters or incomplete/scoped base snapshots cannot supply this fallback.
Historical status cannot be derived from an actionable temporal-view snapshot:
the server changes its row universe and date expression for that request.
Comma-separated task types follow the API's trimmed membership semantics.
The omitted limit follows the API default of 50. Today-task previews never borrow
a plot-scoped snapshot as a whole-garden list.

Regression check: `node scripts/test_task_cache.mjs` from the project root.
