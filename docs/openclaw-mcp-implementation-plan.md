# GardenOps Agent MCP Implementation Plan

**Status:** roadmap retained; unauthenticated-source bridge disabled

**Specification:** `docs/openclaw-mcp-spec.md`

**Strategy:** prove the OpenClaw boundary, then deliver additive vertical slices
on a separate disabled agent-v1 MCP runtime

## 0. Current implementation decision

The former lean stdio bridge is disabled. Its shared bearer and fixed account
binding could authenticate the local process, but could not prove which Matrix
room and sender caused an OpenClaw tool invocation. The bridge and its REST
authorization path now fail closed until immutable connector-authenticated
source provenance is supplied and checked on every request.

The retained implementation consisted of:

1. `gardenops.agent_mcp_stdio`, containing `garden_capabilities`,
   `garden_identify_plant`, `garden_read`, and `garden_write`;
2. the existing MCP bearer in a private file, never inline in OpenClaw config;
3. loopback calls into the existing GardenOps REST/domain implementation,
   including the PlantNet-first identification endpoint for images staged under
   the configured Matrix media root;
4. a server-side method/path allowlist plus fixed user/garden membership
   resolution for every request;
5. OpenClaw per-agent policy exposing the tools to `matrix-lads` and denying
   them to other configured agents; and
6. retirement of the legacy GardenOps Matrix worker after live MCP acceptance,
   avoiding duplicate room responses.

Acceptance requires focused auth/MCP tests, the broader backend suite, an
independent Sol 5.6 Ultra review, a clean production build/preflight, a live MCP
probe, a real read through `matrix-lads`, Matrix channel health, and disabling
the legacy worker. No database migration is required.

The remaining sections record the stronger multi-user roadmap and its prior
adversarial findings. They are not claims about the lean profile's implemented
event-level provenance or proposal machinery.

## 1. Goal and roadmap

The eventual product goal remains broad non-platform GardenOps parity through
the existing OpenClaw `matrix-lads` agent. The first releasable product is
deliberately narrow so the novel trust and delivery boundary is proven before
it carries production garden authority.

A separate audited capability inventory classifies every HTTP/UI operation as:

- `v1` — implemented and present in the runtime policy;
- `roadmap` — intended operational parity, not yet callable;
- `visual-only` — requires the web/map interface;
- `step-up-web-only` — requires session MFA/reauthentication;
- `retired`; or
- `excluded` — platform/security/host administration.

The machine-readable runtime policy contains only shipped actions. It is not a
speculative mirror of every route.

## 2. Non-negotiable gates

1. **OpenClaw plugin feasibility:** host-owned source event, final tool call,
   attachment, reply, and outbound delivery identities correlate correctly
   under concurrent turns.
2. **Agent isolation:** optional GardenOps tools are absent outside
   `matrix-lads`; the shared OS-user threat boundary is explicit and tested.
3. **Attestation:** final tool and canonical arguments are signed with fresh,
   replay-protected event context outside model-authored arguments.
4. **Proposal visibility:** the plugin sends GardenOps' immutable proposal
   rendering unchanged and persists/reconciles its Matrix event ID before apply.
5. **Domain parity:** REST and MCP call the same service and preserve required
   audit, transaction, outbox, notifications, automations, and history.
6. **Effect safety:** PostgreSQL-derived idempotency, one direct operation per
   event, exact proposal reply, and stale read-set rejection are authoritative.
7. **Media safety:** no model-authored path; no-follow descriptor relay,
   content hashing, bounds, destination restriction, and cleanup are proven.
8. **Cutover safety:** single ingress writer, legacy queue drain, sync-token
   watermark, fencing generation, and replay-safe rollback are proven.

Failure of Gate 1 blocks source-bound reads, mutations, proposal apply, and
media. It does not justify weakening the contract.

## 3. Architecture and repository boundaries

```text
OpenClaw native plugin
  optional tools + concurrent-turn correlation + immutable Matrix delivery
  attestation + protected credentials + attachment/resource relay
        |
        v
GardenOps /mcp/agent-v1 (separate feature flag, bearer, namespace, edge rule)
        |
        v
principal/policy -> query and operation services -> shared domain services
        |
        v
PostgreSQL + transactional outbox + audit + media
```

Expected code locations:

- `tools/openclaw-gardenops-plugin/`: native probe/plugin; no GardenOps business
  logic or database access;
- `gardenops/agent_mcp_server.py`: separate thin v1 MCP adapter;
- `gardenops/agent_models.py`: strict versioned input and discriminated output;
- `gardenops/services/agent_identity.py`: principal and attestation checks;
- `gardenops/services/agent_policy.py`: shipped-action registry;
- `gardenops/services/agent_queries.py`: bounded shared queries;
- `gardenops/services/agent_operations.py`: events, calls, operation recovery,
  proposals, apply, and outbox coordination;
- existing/new domain services: behavior shared by REST and MCP;
- `gardenops/services/agent_media.py` and integration routes: bounded binary
  relay and artifact handles.

The legacy `/mcp`, bearer, `assistant_*` tools, Matrix worker, and
`assistant_requests` remain isolated. They are never advertised through the new
plugin and are not silently upgraded to the new trust model.

## 4. Persisted state design

Use additive migrations only after the plugin feasibility gate passes. Separate:

1. immutable authenticated source events and their one-direct-operation slot;
2. individual attested tool calls and nonces;
3. durable domain operations, request hashes, and replayable terminal results;
4. proposals with target/read set and initiation/confirmation identities;
5. immutable proposal/output deliveries, attempts, acknowledgements, and
   unknown-outcome reconciliation;
6. pending assets and resource handles;
7. principal credentials and rotation/revocation metadata.

PostgreSQL uniqueness fences effects. Redis may cache freshness/rate decisions
but is never authoritative for mutation idempotency. Short-lived event/call
replay state defaults to 48 hours. Compact operation, delivery, result, and audit
evidence follows the longer GardenOps retention policy.

Lock order is principal/event, operation or proposal, domain aggregate, then
target public IDs sorted lexically. Concurrency is enabled one domain at a time:
start with tasks and their existing monotonic revision. Collections gain a
per-garden/per-domain epoch updated by every REST and MCP writer before their
mutation actions are enabled. Spatial changes additionally use the existing
garden-layout advisory lock.

Domain rows, operation journal, required audit, and transactional outbox commit
together. External provider, Matrix, callback, and notification effects are
at-least-once, idempotent, observed, and reconciled—not transactionally atomic.

## 5. Delivery slices

### Slice 0A — OpenClaw feasibility harness

Build a non-production native OpenClaw plugin probe with no GardenOps data,
endpoint, credential, schema, or mutation. Against a pinned OpenClaw version,
prove:

1. host-owned sender/room/event/reply/text/media context joins the exact final
   optional tool call under two concurrent turns;
2. arguments are canonicalized and signed after final model selection;
3. tamper, missing context, stale envelope, nonce replay, cross-turn binding,
   wrong source, and wrong agent fail closed;
4. the plugin sends one immutable synthetic proposal using a deterministic
   Matrix transaction ID, records or reconciles the provider event ID, and
   binds a later exact affirmative reply;
5. the model never receives the hidden media host path; the plugin can open and
   hash a synthetic staged attachment descriptor safely;
6. the optional probe tool is visible only to `matrix-lads`, including a
   synthetic future-agent test;
7. raw endpoint/socket and sibling-tool attempts cannot acquire probe authority
   within the declared OpenClaw/OS trust boundary.

Exit: an automated fixture and evidence transcript prove the join; the exact
plugin API, concurrency key, delivery reconciliation, media mechanism, version
floor, and limitations are documented. No production installation or gateway
restart occurs in this slice.

### Slice 0B — freeze narrow v1 contracts

1. Threat-model plugin, Matrix ingress/delivery, model, sibling agents, shared
   OS account, agent-v1 MCP, GardenOps, providers, and media/artifacts.
2. Freeze canonical JSON/Unicode/text/date/decimal/unit algorithms and signed
   test vectors.
3. Freeze principal format, credential hashing/rotation/revocation, state
   machines, lock order, limits, metrics, egress policy, transcript retention,
   and separate v1 endpoint/edge policy.
4. Create the full capability inventory, but put only two actions in v1:
   `garden_overview` and proposal-backed completion of one task.
5. Direct natural-language mutations, media, and sensitive data egress remain
   disabled.

### Slice 1 — separate v1 runtime and `garden_overview`

1. Add MCP-specific principal configuration independent of legacy `MATRIX_*`,
   binding one client/agent/account/room/sender/user/garden.
2. Add disabled `/mcp/agent-v1` with distinct credential, tool namespace, and
   edge denial.
3. Add a v1-only policy registry that rejects unregistered actions.
4. Add strict schema-versioned input and closed status-discriminated output.
5. Extract one shared query returning only garden identity and bounded aggregate
   counts. No notes, precise locations, media metadata, weather/provider calls,
   refresh, metering, or cleanup.
6. Register `garden_overview` with `readOnlyHint=true`,
   `openWorldHint=false`, strict unknown-field rejection, and 16 KiB hard output.

Exit: actual `tools/list` schema, malformed call, REST/MCP parity, read-only DB,
membership recheck, cross-source/garden/agent, result-cap, and legacy-isolation
tests pass. Live use is synthetic-canary-garden only until egress controls pass.

### Slice 2 — one proposal-backed task completion

1. Add normalized source/call/operation/proposal/delivery state and constrained
   transitions.
2. Verify signed envelopes, freshness, nonce replay, and source policy.
3. Add one `garden_propose_change` union variant: complete one task.
4. Reuse the existing task revision and task-completion domain behavior.
5. GardenOps creates an immutable display payload; the plugin sends it unchanged
   and records/reconciles the exact Matrix event ID.
6. Add get/apply/cancel and operation recovery. Apply requires a later narrow
   affirmative reply to that exact event.
7. Commit task rows, operation, required audit, and outbox atomically; reconcile
   external effects after commit.

Exit: altered args, replay, second mutation, stale task, revoked role, negative
reply, multiple proposal, lost acknowledgement, plugin crash, and fault points
around commit/send/ack all fail or recover without duplicate completion. Direct
completion remains disabled.

### Slice 3 — useful reads and everyday proposal-backed writes

Add `garden_search`, `garden_get`, `garden_today`, calendar, cached weather, and
reports incrementally with sensitivity labels and cumulative egress budgets.

Add one mutation domain at a time using extracted shared services:

- journal/observations and harvest;
- complete task management;
- plants, growing state, and placements;
- issues and follow-up;
- inventory and planting from stock;
- procurement and manual calendar events.

Each lands with REST/MCP side-effect, authorization, idempotency, and concurrency
parity. Direct execution is promoted per action only after a finite versioned
matcher independently derives action, unique target, value, date/unit, and
cardinality from canonical user-only text. Otherwise the broad capability stays
proposal-only.

### Slice 4 — media, analysis, and complete operational parity

- plugin attachment relay and pending-asset lifecycle;
- identification, diagnosis, reference lookup, and care generation;
- plots, canonical containers, zones, map objects, batch placement, and layout;
- task generation and seasonal workflows;
- attention, notification personal state, planner goals, and saved views;
- supported garden/calendar/layout/ShadeMap settings;
- bounded import preview/apply where no session step-up is required;
- supported export and snapshot artifacts through principal/room-bound TTL
  handles.

Session-step-up operations remain web-only. Retired map units and unsupported
backup restore are never exposed.

### Slice 5 — live canary and cutover

1. Deploy additively with agent-v1 mutations disabled.
2. Prove effective optional-tool visibility for every active and a future
   synthetic agent, plus unauthorized plugin/raw endpoint attempts.
3. Run bounded reads against a synthetic canary garden, then explicitly approve
   production read promotion after egress/transcript verification.
4. Enable proposal-backed actions one domain at a time with exact UI/database/
   audit/outbox/side-effect readback.
5. Run photo, ambiguity, replay, stale, negative confirmation, role revocation,
   provider failure, concurrent-turn, and plugin-restart canaries.
6. Make the legacy worker consume the shared ingress fence. Drain its queue,
   require zero active legacy proposals/assets or explicitly expire them, record
   Matrix sync-token watermark and fencing generation, then switch ownership.
7. Observe the agreed soak and prove rollback from the same watermark without
   duplicate response or mutation before deprecating legacy tools.

Gateway interruption is never implicit. Prefer supported reload; explain impact
and obtain fresh approval before any gateway stop or restart.

## 6. Limits, observability, and egress

Implement the specification's central defaults and hard maxima for envelope
age/skew, argument/result bytes, rows/pages/bytes per event/session/day,
concurrency, proposals, attachments, exports, and TTLs. Cursors are signed,
keyset-based, query/principal/policy-bound, and carry remaining budget.

Metrics cover attestation/signature/replay failures, policy and cross-agent
denials, egress and rate limits, stale conflicts, operation/proposal/delivery/
asset states, outbox backlog, provider use, and cutover fencing.

Sensitive fields require an independently checked attested-text query grant and
allowed model/provider at call time. `never-agent` fields never cross the tool
boundary. Document OpenClaw transcript retention, redaction, and deletion before
production garden data is enabled.

## 7. Verification

Automated suites cover:

- actual MCP catalog/schema and malformed calls, not only Pydantic models;
- cross-language canonicalization vectors and Unicode/JSON/date/decimal/unit
  fuzzing;
- policy registry completeness for shipped actions and default denial;
- source/principal/role/garden/agent isolation and future-agent configuration;
- state-machine/property tests and PostgreSQL uniqueness;
- cumulative pagination/context abuse and transcript/log redaction;
- two concurrent Matrix turns and cross-binding attempts;
- fault injection before/after DB commit, outbox, send, and acknowledgement;
- fixed lock order, stale/predicate concurrency, and REST/MCP races;
- attachment no-follow/change-race/MIME/size/quota/cleanup behavior;
- migration, old-binary compatibility, downgrade, cutover, and rollback;
- E2EE reply/thread/edit/redaction/media behavior against the pinned real
  OpenClaw/Matrix integration.

Tests use the disposable database and mock AI, PlantNet, weather, Matrix, and
OpenClaw unless explicitly designated as operator-run live acceptance.

## 8. Change and release discipline

Each slice receives focused tests, adversarial review appropriate to its trust
boundary, Ruff/format/environment/integrity checks, complete diff and secret/
generated-artifact inspection, and proportional broader tests.

Do not combine a gateway/config cutover with a large GardenOps domain change.
Do not disable the legacy worker in the release that first enables agent-v1
mutations. Deploy only from current `main` through the documented guarded path.

## 9. Authorized first implementation

Implementation starts only with Slice 0A in this repository:

- create a non-production OpenClaw plugin probe package;
- define its strict probe config and optional tool contract;
- implement in-memory correlation primitives for source events and final calls;
- add canonical argument/signature test vectors and fail-closed unit tests;
- add a delivery-correlation interface and fake adapter tests;
- document exact unresolved host hooks discovered by the executable probe.

It must not read GardenOps data, add migrations, register a production endpoint,
install/enable the plugin, modify OpenClaw configuration, or restart the gateway.
