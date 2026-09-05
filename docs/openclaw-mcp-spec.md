# GardenOps Agent MCP Specification

**Status:** hardening roadmap; lean bridge disabled pending source attestation

**Primary client:** the existing OpenClaw `matrix-lads` agent (LadsBot)

**System of record:** GardenOps

**Primary interaction:** ordinary messages and attachments in the agent's Matrix room

## 0. Disabled lean profile

The prior single-user stdio profile is disabled because agent identity and a
shared bearer do not authenticate the Matrix event that caused a tool call.
GardenOps rejects its REST authorization path and the bridge refuses to start
until the connector can provide immutable, authenticated room and sender
provenance. The items below describe the retired profile, not an available
deployment option:

- OpenClaw's existing `matrix-lads` agent is the trusted conversational
  principal. The closed Matrix room is implicit; no `!garden` prefix is needed.
- A three-tool stdio MCP bridge exposes capabilities, bounded reads, and one
  generic allowlisted mutation tool.
- The bridge calls existing GardenOps HTTP routes over loopback. GardenOps
  remains authoritative for request schemas, user/garden authorization, roles,
  revisions, domain transactions, audit, notifications, and automations.
- The existing `MCP_BEARER_TOKEN` authenticates the bridge. GardenOps accepts it
  only from loopback, re-resolves the configured Matrix user/garden membership
  on every call, rejects garden overrides, and applies a method/path allowlist.
- Authentication, platform administration, memberships, garden creation or
  deletion, imports, backup restore, subscription-token minting, and internal
  maintenance are not exposed.
- Deletes and bulk operations require the MCP call's explicit `confirmed=true`
  gate. Normal writes are permitted when LadsBot determines the user's message
  clearly requests them. Existing endpoint-specific revision and idempotency
  contracts remain in force.

This profile intentionally does not implement the later connector-signed
per-event attestation, durable proposal delivery state machine, binary media
relay, or separate `/mcp/agent-v1` runtime described below. Those remain useful
future hardening for a multi-user or mutually untrusted agent deployment, but
are not prerequisites for the current same-host, single-owner LadsBot setup.

## 1. Outcome

LadsBot becomes a complete conversational interface to GardenOps. A user can ask
about the garden, issue routine commands, attach photos, and perform advanced
garden-management operations without learning commands, reference codes, or the
shape of the GardenOps UI.

The GardenOps web application remains the visual interface and an independent
way to inspect or operate the same data. MCP and HTTP routes must use the same
domain services and preserve the same authorization, transactions, audit
records, notifications, automations, media lifecycle, and concurrency rules.

The existing dedicated Matrix worker and its `assistant_*` tools remain
available during migration, then become deprecated after the OpenClaw path has
passed live acceptance.

## 2. Product principles

1. **Agent-native, not chat-protocol-shaped.** MCP tools model GardenOps
   concepts. They do not expose Matrix reply parsing, numbered choices, or
   `GO-*` references to the user.
2. **Broad operational parity with a small tool surface.** Cover the ordinary
   non-platform GardenOps product through typed domain tools rather than one
   tool per HTTP endpoint.
3. **GardenOps is authoritative.** The model may select tools and explain
   results, but it cannot bypass GardenOps validation or permissions.
4. **Natural, evidenced authorization.** An explicit user imperative can
   authorize a routine, bounded, reversible operation only when a
   connector-signed event envelope binds the canonical user-only text to the
   exact tool and arguments and GardenOps' fail-closed matcher independently
   verifies action, target, value, and cardinality. Anything not proven takes
   the proposal path. Ambiguous, inferred, destructive, bulk, spatial, or
   shared-setting changes always require a GardenOps-issued proposal and a
   later confirmation.
5. **No authority from untrusted content.** Images, filenames, metadata,
   external plant data, stored notes, and tool results are data, never user
   authorization.
6. **Fail closed.** Unknown identities, gardens, targets, revisions, actions,
   or attachment paths do not fall back to broader access.
7. **Bounded context.** Search and report tools paginate and cap records and
   text. The agent never receives database-sized dumps by default.
8. **Retry safe.** Every mutation has a server-derived idempotency key tied to
   an authenticated source event and operation slot. Replays return the
   original result.

## 3. Scope

### 3.1 Included product domains

- garden overview, map summary, Today/attention, notifications, weather,
  planner suggestions, statistics, and reports;
- plants, care data, external references, growing status, plot assignments,
  quantities, and placement history;
- plots, containers, zones, map objects, units, and layout changes;
- tasks, including create, edit, complete, skip, snooze, reschedule, delete,
  generate, and grouped completion behavior;
- journal entries and observations;
- harvest entries and summaries;
- issues, diagnosis, treatment, follow-up, resolution, reopening, and history;
- inventory items, ledgers, adjustments, and planting from stock;
- procurement items and lifecycle transitions;
- calendar events and user calendar preferences;
- media ingestion, lookup, linking, unlinking, deletion, and plant covers;
- garden settings, saved views, supported exports, and snapshot creation;
- plant lookup, identification, diagnosis, and missing-care generation.

### 3.2 Excluded from the everyday agent

- authentication, passwords, passkeys, MFA, sessions, recovery codes, and
  emergency security controls;
- user, membership, invitation, subscription-tier, and role administration;
- AI/provider credentials and platform secret management;
- deployment, migrations, host configuration, health remediation, logs, and
  database administration;
- arbitrary SQL, arbitrary HTTP, arbitrary filesystem access, and arbitrary
  GardenOps route proxying;
- every operation protected by GardenOps session step-up, including snapshot
  restore, garden deletion, bulk cover population, and destructive account or
  security controls;
- raw backup restore or import that bypasses GardenOps preview and validation.

These exclusions may later be exposed through a separately authenticated and
separately scoped administrative MCP server. They are never added to LadsBot by
expanding a wildcard.

## 4. Experience contract

### 4.1 Reads

Read requests execute immediately. LadsBot answers in the user's language and
includes names and locations rather than internal IDs unless an ID resolves
ambiguity.

Examples:

- “What needs attention this weekend?”
- “Where is the kransvakkerøye planted?”
- “What is growing in the greenhouse?”
- “Show the last three things we did to the blackcurrants.”
- “How much seed compost is left?”
- “What orders have not arrived?”
- “How was the tomato harvest this year compared with last year?”

When several records match a read, return the useful set. Do not force a choice
unless the requested answer or a subsequent mutation needs one exact target.

### 4.2 Routine explicit changes

A bounded routine operation may execute without a second confirmation when the
current authenticated user message explicitly requests that exact operation,
all required targets and values resolve unambiguously, the connector signs the
event/tool/argument binding, and GardenOps independently matches the canonical
user-only text to the requested action, target, value, and one-record
cardinality. Failure to prove any element returns a proposal; it never guesses.

Examples include recording an observation or harvest, completing one task,
snoozing one task to a stated date, adjusting one inventory item by a stated
amount, or creating one manual calendar event.

One source event grants at most one direct durable domain operation. Additional
agent calls under the event may only be reads or declared atomic side effects of
that operation. The result states exactly what changed and exposes an operation
ID for audit and support, not as a conversational command the user must repeat.

### 4.3 Proposals

GardenOps must return a proposal rather than execute when any of these apply:

- target, location, quantity, date, unit, or intended action is ambiguous;
- the operation is inferred from an image or other untrusted content;
- the operation deletes, archives, restores, imports, overwrites, or moves a
  spatial object;
- more than one durable record will be changed, except documented atomic side
  effects of one requested operation;
- a shared garden setting changes;
- GardenOps calculates non-obvious impact;
- the target revision has changed since it was read;
- policy explicitly classifies the action as confirmation-required.

A proposal contains a human summary, exact target IDs and revisions, before and
after fields, calculated impact, warnings, expiration, and an opaque proposal
ID. It persists no garden-domain change.

Only a later authenticated source event may apply it. GardenOps produces an
immutable versioned display payload and digest containing the complete target,
before/after values, impact, and warnings. The OpenClaw plugin sends that
payload unchanged and GardenOps stores its acknowledged outbound Matrix event
ID and render digest before the proposal becomes confirmable. Unknown delivery
outcome remains unconfirmable until reconciled. Confirmation
must be a reply to that exact event and match a narrow connector-side grammar
such as `yes`, `confirm`, or `save`; negatives and additional instructions do
not match. The connector signs the confirmation event, reply relation, proposal
ID, tool, and argument digest. There is no “currently active proposal” fallback.
If the installed OpenClaw channel cannot provide authenticated inbound reply
relations and outbound send acknowledgements, proposal apply remains disabled.
`cancel` discards a proposal. Expired or stale proposals must be regenerated.

### 4.4 Clarification

The agent asks one concise natural question and presents meaningful choices.
The user may answer using a name, location, description, or ordinal. Internal
reference codes remain hidden.

### 4.5 Multi-step requests

LadsBot may compose reads to satisfy one request. At most one direct durable
operation grant exists per source event. A multi-record or compound mutation is
one proposal-backed GardenOps domain operation with a declared target set and
atomic transaction, not several calls that evade cardinality limits.

Example: identify a plant, find its placement, diagnose damage, prepare an
issue, schedule its follow-up, and link the image. The diagnosis and lookup are
reads; the issue plus follow-up is one atomic GardenOps operation.

## 5. MCP interface

All schemas are strict and versioned. Unknown fields are rejected. Dates use
ISO `YYYY-MM-DD`, timestamps use RFC 3339, quantities preserve domain precision,
and records use existing GardenOps public IDs.

Every JSON result is a closed discriminated union keyed by `status`, with
status-specific required and forbidden fields. Binary media and exports use the
resource-handle contract in section 9. Inputs and outputs both carry an explicit
schema version; breaking contracts use new tool versions/names.

```json
{
  "schema_version": 1,
  "status": "ok|needs_input|proposal|applied|conflict|error",
  "summary": "short human-readable result",
  "data": {}
}
```

- `ok` requires `data` and may include `next_cursor`.
- `needs_input` requires non-empty typed `choices` and has no operation result.
- `proposal` requires the immutable proposal display payload and digest.
- `applied` requires `operation_id`, affected records, and safe result.
- `conflict` requires a stable conflict code and fresh read/proposal guidance.
- `error` requires a stable error code and `retryable`; it has no partial data.

Transport/protocol/authentication/schema failures set MCP `isError=true` and do
not return a domain success union. Expected domain states such as ambiguity,
proposal, stale conflict, and policy denial return structured content with
`isError=false` so clients can handle them deterministically.

Errors contain stable machine codes and safe messages. They never contain
credentials, SQL, host paths, raw provider responses, or stack traces.

### 5.1 Discovery tools

1. `garden_overview`
   - Active garden identity, season, counts, map/plot summary, data quality,
     current weather summary, and high-level operational state.
2. `garden_search`
   - Cross-domain or domain-filtered search over plants, placements, plots,
     tasks, issues, journal, harvests, inventory, procurement, calendar, and
     media.
   - Inputs: query, record types, statuses, plant/plot links, date range,
     limit, cursor.
3. `garden_get`
   - Complete bounded detail for one typed public ID, including requested link
     expansions and current revision.
4. `garden_today`
   - Existing attention model, due work, follow-ups, weather risks, notices,
     and no-action-needed outcomes for a date or short range.
5. `garden_calendar`
   - Generated and manual events with plant, plot, zone, type, and date filters.
6. `garden_weather`
   - Forecast, alerts, frost/dryness signals, and plant-aware risks.
7. `garden_report`
   - Named bounded report types: seasonal summary, harvest, planting history,
     bloom windows, area use, data quality, issues, upcoming work, inventory,
     and procurement.

### 5.2 Direct routine operation tools

Direct tools are deliberately narrow so their static MCP annotations and
authorization matcher are truthful. The model supplies domain arguments only;
the connector derives provenance, the operation slot, and idempotency key.

1. `garden_record_observation`
2. `garden_record_harvest`
3. `garden_complete_task`
4. `garden_snooze_task`
5. `garden_adjust_inventory`
6. `garden_create_calendar_event`

Each operates on one unambiguous primary record or creates one primary record.
Normal linked journal, task-history, notification, automation, ledger, and
media effects remain declared atomic side effects rather than extra operation
slots. If direct authorization cannot be proven, the tool returns a prepared
proposal without changing garden-domain state.

### 5.3 Proposal-only operation surface

`garden_propose_change` exposes the full action catalog through a strict
versioned discriminated union. It never performs the garden-domain mutation.
Supported domains are:

- plant: create, update, growing status, generate care, delete;
- placement: assign, quantity, move, remove, batch move;
- task: create, update, complete, skip, snooze, reschedule, delete, generate;
- observation/journal: create, update, delete, batch create;
- harvest: create, update, delete;
- issue: create, update, resolve, reopen, dismiss, delete;
- inventory: create/update item, ledger transaction, plant from stock, delete;
- procurement: create, update, transition, delete;
- calendar: create, update, delete manual event and preference changes;
- map: create/update/archive plots and canonical containers, map objects,
  layout moves, zones, and supported spatial settings;
- media: attach, detach, delete, and set cover;
- personal state: attention, notification, planner goal, and saved views;
- garden settings, workflows, supported import preview, export creation, and
  snapshot creation.

Retired map-object units are not exposed. Canonical containers are used.
Session-step-up operations remain denied, not merely proposal-gated.

### 5.4 Specialist analysis tools

1. `garden_identify_plant`
2. `garden_diagnose_plant`
3. `garden_lookup_plant_reference`

These reuse GardenOps provider controls, budgets, and media validation. Their
output is advisory and cannot itself authorize a mutation. General
`garden_chat` is not exposed: LadsBot composes structured GardenOps reads and
does the conversational reasoning.

### 5.5 Proposal and operation tools

1. `garden_proposal_get`
2. `garden_proposal_apply`
3. `garden_proposal_cancel`
4. `garden_operation_get`

`garden_proposal_apply` requires the exact acknowledged proposal-message reply
and connector-attested confirmation described in section 7. Apply locks the
proposal and target/read-set rows, verifies revisions and authorization again,
writes atomically, and stores the result for idempotent replay.

`garden_operation_get` recovers the durable safe result after a lost or
ambiguous acknowledgement. The model cannot choose a new idempotency key.

The first mutation release is proposal-only. Direct routine tools remain
disabled until a finite versioned authorization grammar independently derives
the same action, unique target, value, date/unit interpretation, and one-record
cardinality from canonical user-only text. The grammar initially accepts no
pronouns or conversationally inferred referents. Unicode normalization, locale,
decimal strings, dates, and units are specified as test vectors before direct
execution is enabled action-by-action.

### 5.6 Tool annotations

- Pure cached discovery tools use `readOnlyHint=true`.
- Weather refresh, provider-backed analysis, task/care generation, and any
  cache- or meter-writing call use `readOnlyHint=false`; external-provider
  tools also use `openWorldHint=true`.
- Additive observation, harvest, and calendar-create tools may use
  `destructiveHint=false`. Completion, snooze, adjustment, update, move,
  remove, and delete tools use `destructiveHint=true`; reversibility does not
  make an overwrite non-destructive under MCP semantics.
- `garden_propose_change` uses `destructiveHint=false` because it only adds
  proposal metadata and cannot change garden-domain state.
- `garden_proposal_apply` uses `destructiveHint=true` because it may update or
  delete garden-domain state.
- Annotations are planning hints, never authorization. Direct tools do not
  claim `idempotentHint=true` merely because one source event is replay-safe;
  the same visible arguments in a later event may legitimately create another
  effect.

## 6. Identity and authorization

### 6.1 MCP principal

MCP configuration is independent of Matrix-worker configuration. A configured
MCP principal resolves to exactly one active GardenOps user and garden
membership and includes:

- stable client ID;
- GardenOps user ID and garden ID;
- platform and garden roles;
- allowed source channel/account/room/sender;
- allowed tool/action classes;
- credential identifier and rotation metadata.

The principal is derived from the authenticated credential and server-side
configuration. The model cannot choose a username, garden, role, or scope.

The first release loads one principal from a protected host-owned JSON file.
The file stores client/agent/source allowlists, GardenOps username/garden slug,
timezone and locale, action allowlist, credential hash with key ID, attestation
public/key identifier, activation time, and optional revocation time. Startup
validates the complete record and resolves the active membership. Every call
rechecks membership and role. Rotation permits two named credential/key IDs for
a bounded overlap; revocation takes effect on the next call.

The authorized Matrix account, room, sender, and OpenClaw agent are the source
identity boundary. E2EE device verification is required operationally but does
not replace sender/room/account checks. Relative dates use the principal
timezone, then garden timezone if one is introduced; host timezone is never an
implicit fallback. Matrix origin time is recorded, while server receive time
governs freshness and authorization windows.

Viewer principals receive only reads. Editor/admin behavior continues to obey
the existing ownership and role rules. Platform-admin-only endpoints do not
become accessible merely because the bound user is a platform admin.

### 6.2 OpenClaw scope

The GardenOps integration is an OpenClaw plugin whose tools are optional and
absent unless explicitly allowlisted for `matrix-lads`. The plugin checks
`agentId` and source context at runtime as defense in depth. GardenOps v2 is not
registered as a global OpenClaw MCP server.

Deployment aborts unless authoritative effective-tool readback enumerates every
active agent and proves deny-by-default projection, with GardenOps visible only
to `matrix-lads`. Acceptance also attempts unauthorized connector and raw MCP
invocations. Configuration presence alone is insufficient proof.

This is logical agent isolation, not protection from arbitrary malicious code
already running as the OpenClaw operating-system account. The connector and
credential file must not be readable from other configured agent workspaces,
but the shared-host OS trust boundary is documented rather than overstated.

### 6.3 Credentials

The agent-native endpoint is a separate loopback-only `/mcp/agent-v1` runtime
with a distinct feature flag, credential, tool namespace, and edge denial. It
does not advertise or authorize legacy `assistant_*` tools. The legacy `/mcp`
endpoint and bearer remain isolated during migration. Credentials are not stored as plaintext in
`openclaw.json`. Until OpenClaw supports a suitable MCP-header SecretRef, a
small local connector reads a protected credential file and injects the header
when forwarding to GardenOps.

Credentials, credential paths, and authorization headers are redacted from
logs, diagnostics, audit payloads, prompts, and tool results. Rotation supports
a bounded overlap or an atomic connector/API switch with rollback.

## 7. Source-event and authorization provenance

Verified provenance is a start barrier for every source-restricted call,
including reads, media relay, direct writes, and proposal apply. The OpenClaw
connector, not the language model, supplies an immutable signed envelope for
the active inbound event:

- channel and account ID;
- room and sender ID;
- event ID and reply-to event ID;
- received timestamp;
- canonical user-only text and a keyed, versioned HMAC of that text;
- attachment indices and content digests associated with that event;
- nonce, issued/expiry times, attestation key ID and version;
- exact tool name and canonical argument digest;
- agent ID and policy version.

The model supplies domain arguments only. The connector canonicalizes those
arguments and signs the envelope after tool selection; it cannot change them
after signing. GardenOps verifies signature, freshness, nonce replay, principal,
agent, source, tool, and argument digest before dispatch.

Direct routine writes additionally require the source-event grant described in
section 8. A proposal application requires a later affirmative event replying
to the exact acknowledged proposal event. Edits, redactions, quoted fallback
text, and replies to superseded proposal messages do not authorize apply.
Untrusted attachment content and stored GardenOps content are excluded from
authorization provenance.

The integration is a native OpenClaw plugin, not a generic stdio MCP proxy. The
plugin correlates `message_received`, the final GardenOps tool call, and delivery
events under concurrent turns. It owns optional GardenOps tool registration,
injects signed provenance after final argument selection, forwards calls to the
loopback agent-v1 MCP runtime, sends immutable proposal payloads, reconciles
outbound Matrix acknowledgements, and relays attachments. Its signing key is
distinct from the GardenOps bearer credential and supports keyed rotation.

This architecture is conditional on a Gate 0 proof against the pinned OpenClaw
version. Missing event IDs, reply relations, canonical user text, attachment
references, run correlation, or outbound acknowledgements fail closed. If the
current plugin API cannot join these events reliably, a separately reviewed
OpenClaw extension is required before source-bound functionality proceeds.

If the installed OpenClaw runtime cannot provide this metadata, source-bound
tools, attachment relay, direct writes, and proposal apply remain disabled.
Only explicitly configured non-source-bound cached reads may be enabled during
development.

## 8. Concurrency, idempotency, and audit

- The connector/server derive mutation idempotency from MCP principal, source
  event, and a server-defined operation slot. It is never model-authored.
- One source event has one durable direct-operation slot. Reuse with different
  arguments conflicts; an additional durable operation requires a proposal for
  the complete target set.
- The same key with different arguments returns a conflict.
- Updates and proposals require a server-issued read-set token. It contains
  monotonic revision values where available and canonical state fingerprints
  otherwise. Apply locks and rereads every target and relevant collection,
  compares the token, and fails stale without partial effects. Spatial changes
  also take the existing garden layout advisory lock. Aggregate revisions are
  added where predicate/collection changes cannot otherwise be protected.
- Domain rows, operation journal, required audit, and transactional outbox are
  committed atomically. External effects such as Matrix delivery, provider
  calls, callbacks, and notifications are at-least-once, idempotent,
  observable, and reconciled; they are not described as transactionally atomic.
- Apply rechecks current role, ownership, feature availability, targets,
  revisions, and calculated impact.
- Audit entries identify MCP principal, GardenOps actor, channel/account/room/
  sender/event/reply IDs, received time, keyed text HMAC, attestation key and
  version, authorization and policy versions, canonical argument digest,
  proposal initiation/confirmation IDs, affected public IDs, result, and
  request correlation ID without copying full sensitive messages.
- Operation result and canonical request hash survive target deletion. Source
  event uniqueness tombstones are retained indefinitely unless a documented
  upper replay bound permits a shorter period. Ambiguous acknowledgement never
  creates a new key; the client queries `garden_operation_get` first.

## 9. Media boundary

OpenClaw decrypts Matrix media and stages it in managed inbound storage. The
GardenOps service must not receive general access to that storage.

The OpenClaw plugin accepts only an attachment index bound into the signed
current-event envelope. Using descriptor-relative, no-follow file opening, it
opens the plugin-supplied hidden host path beneath the configured inbound-media
directory and computes device/inode/size and digest from the opened descriptor.
Those filesystem facts are connector-computed, not claimed to be supplied by
OpenClaw. It verifies the descriptor is a regular file.
It rejects symlinks, traversal, URLs, special files, stale events, and changed
files, then streams with byte and wall-time caps to a connector-only GardenOps
ingestion endpoint. Upload idempotency is principal + event + attachment index
+ content digest. GardenOps performs its normal size, MIME, decoded-image,
quota, persistence, and cleanup checks.

The connector never accepts a model-authored path. The upload result contains
an opaque GardenOps asset ID. Pending assets are linked atomically to a created
record or cleaned after expiry/cancellation, connector crash, or upload failure.

Exports create an audited opaque TTL resource handle containing sanitized
filename, MIME type, exact size, and checksum. The connector streams the handle
to Matrix with byte/time caps and records the outbound event acknowledgement.
Backup exports and files requiring session step-up remain web-only.

Pending assets have explicit `receiving`, `ready`, `linked`, `expired`, and
`failed` states with constrained transitions. Resource handles are one-time,
principal-bound, room-bound, and short-lived. Unknown Matrix delivery outcomes
remain pending until readback/reconciliation; they are not resent with a new
transaction identity.

## 9.1 Default limits

All values are configurable downward; hard maxima require a reviewed code
change. Rate limits are per principal unless stated otherwise.

| Limit | Default | Hard maximum |
|---|---:|---:|
| Attestation age / future skew | 60 s / 5 s | 300 s / 30 s |
| Tool arguments | 32 KiB | 128 KiB |
| JSON tool result | 16 KiB | 64 KiB |
| Search page / pages per event | 20 / 5 | 100 / 10 |
| Rows per event / session / day | 100 / 500 / 5,000 | 500 / 2,000 / 20,000 |
| Result bytes per event / session / day | 64 KiB / 512 KiB / 10 MiB | 256 KiB / 2 MiB / 50 MiB |
| Concurrent reads / mutations | 4 / 1 | 8 / 1 |
| Proposals per event / pending principal | 1 / 10 | 1 / 50 |
| Pending attachments | 10 | 50 |
| Attachment bytes / upload time | 5 MiB / 30 s | 20 MiB / 120 s |
| Export bytes / handle TTL | 10 MiB / 10 min | 50 MiB / 60 min |
| Proposal TTL | 30 min | 24 h |
| Source-event/nonce replay rows | 48 h | 30 d |

Minimal operation identity, request digest, terminal result, delivery identity,
and audit evidence outlive nonce rows according to the normal GardenOps data
retention policy. PostgreSQL uniqueness is the mutation effect fence; Redis may
accelerate rate/freshness checks but is never authoritative for idempotency.

Metrics cover attestation/signature/replay failures, policy denials, cross-agent
attempts, query egress, rate limits, stale conflicts, operation/proposal states,
pending assets, delivery reconciliation, outbox backlog, provider usage, and
cutover fencing.

## 10. Service architecture

```text
Matrix client
    -> OpenClaw Matrix channel and E2EE
    -> matrix-lads / LadsBot
    -> GardenOps OpenClaw plugin (correlation, optional tools, attestation,
       immutable proposal delivery, credential and attachment relay)
    -> separate loopback Streamable HTTP MCP /mcp/agent-v1
    -> MCP principal and policy
    -> shared GardenOps query/command services
    -> PostgreSQL, media store, audit, notifications, automations
```

Router handlers and MCP handlers are adapters. Business SQL and side effects
must live in shared services. Existing domain commands are retained and
expanded incrementally. Cross-domain reads receive dedicated query services
rather than invoking FastAPI routes internally.

The connector does not contain GardenOps business logic, AI interpretation, or
database access.

## 11. Compatibility and migration

1. Prove the OpenClaw plugin correlation and delivery mechanism without
   GardenOps data or mutation.
2. Add the separate disabled agent-v1 MCP runtime and forward-compatible
   principal, attestation, call, event, operation, proposal, delivery, and asset
   storage alongside the existing Matrix tables and tools.
3. Keep the existing Matrix worker operating in its existing room during
   development.
4. Give each ingress a single-writer lease. Test LadsBot in its own authorized
   room without giving two bots ownership of the same inbound event.
5. Complete live read, proposal, media, retry, and denial canaries; enable
   direct writes only after their independent grammar is promoted.
6. Before cutover, make the legacy worker consume the shared ingress fence,
   drain its queue, record its Matrix sync-token watermark and fencing
   generation, and
   expire or migrate active proposals/assets, and switch ingress ownership.
7. Disable the dedicated Matrix worker only after acceptance. Rollback resumes
   from the same watermark and shared event-operation journal so it cannot
   double-respond or double-mutate.
8. Retain legacy tools for one release window, then remove the worker-specific
   surface and Matrix-only request constraints in a separate change.

Any OpenClaw gateway interruption requires a separate impact explanation and
fresh operator approval. Prefer a supported MCP reload when sufficient.

## 12. Verification requirements

### 12.1 Automated

- strict input/output schema and pagination tests for every action;
- no garden-domain mutation assertions for pure reads, plus exact allowlists of
  permitted cache, usage-meter, and cleanup effects for impure reads;
- role, ownership, garden, source room/sender, and cross-agent negative tests;
- idempotent replay and same-key/different-payload conflict tests;
- stale revision and locked proposal concurrency tests;
- domain side-effect parity tests between REST and MCP;
- destructive and bulk proposal-required tests;
- attachment handle, traversal, symlink, MIME, decoded-image, size, quota,
  cleanup, and malicious-content tests;
- provider mocks only; no real AI, PlantNet, Matrix, or production database in
  automated tests;
- bounded-output and redaction tests;
- legacy Matrix regression tests until deprecation completes.
- actual `tools/list` input/output schema snapshots, malformed calls, and MCP
  protocol `isError` behavior;
- canonical JSON/Unicode/date/decimal/unit vectors and fuzzing;
- state-machine/property and fault-injection tests around commit, outbox,
  delivery, acknowledgement, retry, and recovery;
- two concurrent Matrix turns, fixed lock order/deadlock tests, cumulative
  pagination abuse, transcript/log leakage, old-binary migration compatibility,
  downgrade, and future-agent isolation tests;
- stored-note, provider-output, image, filename, and metadata prompt-injection
  tests; negative/unclear confirmations; fan-out decomposition; edited/redacted
  events; multiple proposals; role revocation; connector crash and lost
  acknowledgement; concurrent REST/MCP writes; credential rotation; and
  cutover/rollback replay tests.

### 12.2 Live acceptance

Use a written natural-language corpus that covers every included domain and at
least these end-to-end cases:

1. cross-domain question and bounded follow-up;
2. plant location query returning all valid placements;
3. explicit routine observation written once;
4. encrypted photo identification and record linking;
5. ambiguous target clarification;
6. inferred image-based change requiring confirmation;
7. destructive or spatial change with accurate impact preview;
8. stale proposal rejection;
9. same-event and same-key replay behavior;
10. unrelated-agent and wrong-source denial;
11. cancellation/expiry media cleanup;
12. web UI readback showing the exact MCP result and expected side effects.

## 13. Completion criteria

- Every non-platform GardenOps UI operation is mapped to a typed MCP action,
  explicitly classified as visual-only, or explicitly excluded with rationale.
- The agreed conversation corpus passes through the live encrypted Matrix room.
- LadsBot can safely compose reads and operations across domains.
- Routine explicit operations do not require ritual confirmation when trusted
  source provenance is available.
- Ambiguous, inferred, destructive, bulk, spatial, and shared-setting changes
  cannot bypass proposals.
- UI and MCP results, permissions, revisions, audit, and side effects agree.
- Effective tool isolation and credential redaction are proven.
- The dedicated GardenOps Matrix worker can be disabled without losing an
  accepted workflow, and rollback is documented and tested.

## 14. Action policy matrix

The implementation maintains a machine-readable policy registry used by tool
dispatch, schema generation, documentation checks, and tests. Each action names
its required role/ownership, feature gate, execution mode, maximum direct
cardinality, read-set, lock, idempotency slot, declared side effects, MCP
annotations, and output limits. Unregistered actions are denied.

| Domain | Actions | Default mode | Important constraints |
|---|---|---|---|
| Overview/search/get | cached bounded reads | Direct read | Principal-scoped; opaque signed cursor |
| Today/attention | read; mark read, dismiss, snooze, restore | Read direct; changes proposal | Personal state only; generated source effects enumerated |
| Notifications | list/count; read/dismiss/preferences | Read direct; changes proposal | Delivery generation/maintenance denied |
| Weather | cached summary/alerts; refresh; dismiss | Cached read direct; refresh impure; dismiss proposal | External refresh marked open-world |
| Plants | get/search; create/update/growing/care/delete/import preview | Reads direct; changes proposal | Delete destructive; CSV apply proposal and bounded |
| Placements | list; assign/quantity/move/remove/batch | Reads direct; changes proposal | Ownership, row locks, layout/collection token |
| Plots/containers/map | inspect; create/update/archive/move | Reads direct; changes proposal | Canonical containers only; layout lock; retired units denied |
| Journal | list/get; create/update/delete/batch | Read direct; one explicit create may be direct | Observation matcher; side effects declared |
| Harvest | list/get/summary; create/update/delete | Read direct; one explicit create may be direct | Linked journal/automation atomic |
| Issues | list/get/history/summary; create/update/resolve/reopen/dismiss/delete | Reads direct; changes proposal | Diagnosis advisory; follow-up task atomic |
| Tasks | list/get; create/update/actions/delete/generate | Reads direct; explicit single complete/snooze may be direct | Revision required; grouped/batch proposal |
| Inventory | list/get/ledger; create/update/adjust/plant/delete | Reads direct; explicit single adjustment may be direct | Decimal strings; no negative stock; ledger integrity |
| Procurement | list/get/summary; create/update/transition/delete | Reads direct; changes proposal | Valid transition graph; receipt side effects atomic |
| Calendar | list; manual create/update/delete; preferences | Read direct; explicit single create may be direct | Feed-token subscription management denied |
| Planner/workflows | suggestions/profile/goal; set goal/start workflow | Reads direct; changes proposal | Workflow is multi-task atomic/idempotent |
| Saved views | list/presets; create/update/delete | Reads direct; changes proposal | Personal/garden ownership enforced |
| Media | list/get metadata/links; ingest/attach/detach/delete/set cover | Metadata read direct; changes proposal except attested ingest | Bulk cover population denied by step-up policy |
| Analysis | identify/diagnose/reference lookup/generate care | Impure advisory | Provider budget/cache effects; open-world annotation |
| Reports/statistics | named bounded reports | Direct read | Field minimization; resource handle for large artifact |
| Garden settings | read/update supported garden/layout/shade state | Read direct; update proposal | Shared setting; role and layout locks |
| Export/snapshot | supported export; snapshot create | Proposal | TTL artifact; backup export and restore web-only |
| Auth/admin/provider/system | all | Denied | Separate future administrative MCP only |

Policy output limits include maximum rows, expansions, strings, arrays, bytes,
and total result size. Cursors are opaque, keyed, keyset-based, and bound to the
principal, query/filter digest, and policy version.

Dates such as “today” and “this weekend” resolve in the bound user's configured
timezone using the attested event receive time. The policy defines maximum
event age, daylight-saving behavior, locale, decimal-string quantity encoding,
and explicit unit conversion; the model cannot silently substitute host time or
floating-point inventory quantities.

## 15. Data egress policy

Fields are classified `ordinary`, `sensitive`, or `never-agent`. Structured
records sent to LadsBot are minimized to fields needed for the request. Full
notes, precise location data, private media metadata, and export artifacts are
sensitive and require an attested-text query grant checked independently of the
model. Cursors carry the remaining event/query budget and cannot reset it.
`never-agent` fields include credentials, password/session/passkey/MFA data,
raw provider payloads, internal storage paths, and security recovery material.

Deployment specifies the allowed OpenClaw model/provider set and its retention
and logging policy and enforces that model/provider on every sensitive call.
GardenOps provider budgets govern GardenOps specialist analysis only; they do
not govern LadsBot inference. OpenClaw transcript retention/redaction and
operator deletion are documented before production data is enabled. Neither
GardenOps nor the plugin places complete tool payloads in routine logs. Before
these controls pass, live tests use a synthetic canary garden.

## 16. Persisted state separation

The implementation does not conflate source events, individual calls, domain
effects, proposals, deliveries, or assets. It uses normalized stores for:

- immutable authenticated source events;
- individual attested tool calls and their argument digests;
- durable domain operations and replayable terminal results;
- proposals and exact initiation/confirmation relationships;
- outbound deliveries and acknowledgement/reconciliation attempts;
- pending media/assets and resource handles;
- principal credentials and rotation metadata.

Each has a versioned constrained state machine, unique keys, explicit crash
recovery, and bounded retention. Lock acquisition order is principal/event,
operation or proposal, domain aggregate, then target public IDs in sorted order.
