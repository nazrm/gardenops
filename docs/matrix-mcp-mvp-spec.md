# GardenOps Matrix + MCP Assistant — Lean MVP Specification

**Status:** implementation specification
**Target repository:** `nazrm/gardenops`
**Intended repository path:** `docs/matrix-mcp-mvp-spec.md`
**Drafted against:** `main` at `3ec5b15de7315e9027795f33560699ba5759110d` on 2026-09-02
**Primary interface:** Matrix
**System of record:** GardenOps

> The implementation agent must first read `AGENTS.md`, inspect the current branch and working tree, and re-check all referenced files. Current repository code wins over examples in this document. Do not discard unrelated user changes, do not use a live database for tests, and do not call real AI, PlantNet, or Matrix services from automated tests.

## 1. Product goal

A GardenOps user should be able to use a normal Matrix room as the everyday GardenOps assistant interface.

The user can post a photo or message such as:

```text
!garden This has started flowering today.
```

GardenOps should:

1. identify or infer the relevant plant;
2. match it against plants and placements in the configured GardenOps garden;
3. ask a short clarification when the plant or location is ambiguous;
4. present an editable, human-readable proposal;
5. save the proposal only after the user explicitly replies `save`;
6. create the normal GardenOps record and preserve all existing side effects;
7. link the Matrix photo to the resulting GardenOps records.

The user must not need to open the GardenOps web chat for routine observations, harvests, issues, task completion, or garden questions.

## 2. Fixed architecture decisions

These decisions are part of the specification. Do not replace them with a more elaborate platform.

1. **Build MCP from the start.** GardenOps exposes a local MCP server over Streamable HTTP.
2. **Matrix is a thin channel adapter.** The Matrix worker receives events, downloads media, calls GardenOps, and renders responses.
3. **GardenOps owns AI interpretation and garden logic.** Reuse the existing OpenAI/Anthropic provider adapter and PlantNet integration. Do not build a second provider stack in the Matrix worker.
4. **Use a deterministic workflow, not a general autonomous agent loop.** The first version uses typed MCP tools and a small GardenOps state machine. Do not add OpenAI Agents SDK, LangChain, a planner, handoffs, subagents, or arbitrary tool iteration.
5. **One configured Matrix room, one allowed Matrix sender, one GardenOps user, and one GardenOps garden.** Use environment configuration. Multi-user linking and an admin UI are later work.
6. **Matrix is the chat transcript.** Do not add agent session or message-history tables.
7. **Persist only what is needed:** one assistant request/proposal table plus temporary media links in the existing media system.
8. **All durable garden writes require an explicit `save`.** Do not implement automatic writes or reaction-based approval in the MVP.
9. **Use existing GardenOps domain behavior.** Do not duplicate simplified journal, harvest, issue, task, media, notification, or automation SQL in MCP handlers.
10. **Keep MCP private.** It is loopback-only, protected by one static bearer token, and not proxied by nginx.

## 3. Required user-facing scope

The MVP is complete only when all six workflows below work from Matrix.

### 3.1 Garden question

```text
!garden What needs attention this weekend?
```

GardenOps returns a concise garden-aware answer using the configured AI provider and existing garden context.

No GardenOps write occurs.

### 3.2 Photo or text observation

Examples:

```text
[photo]
!garden This has started flowering today.
```

```text
!garden I pruned the blackcurrant in Berry Row today.
```

Supported journal event types:

```text
bloomed
observed
planted
moved
divided
pruned
watered
fertilized
died
```

For an image-inferred bloom, approval must create the normal `bloomed` journal entry and preserve the existing seen-growing side effect.

### 3.3 Harvest

```text
[photo]
!garden Harvested 2.4 kg of Sungold tomatoes from the greenhouse today.
```

If quantity or unit is missing, GardenOps asks for it before producing a proposal.

Approval must use the existing harvest creation behavior, including the linked journal entry and harvest automations.

### 3.4 Plant-health issue

```text
[photo]
!garden What is wrong with this courgette? Save it as an issue if it looks real.
```

GardenOps identifies the plant, runs the existing diagnosis capability, and proposes an issue with:

- issue type;
- title and description;
- severity;
- suspected cause;
- treatment suggestion;
- plant and plot links;
- the photo.

The proposal is advisory until saved.

### 3.5 Task completion

```text
!garden I finished pruning the three blackcurrants in Berry Row.
```

GardenOps searches open tasks and proposes completion only when it can identify the relevant task and target plants. When several tasks fit, it asks the user to choose.

Approval must preserve existing task-completion journal and lifecycle behavior.

### 3.6 Clarification and approval

When several placements match:

```text
GardenOps found Helenium ‘Moerheim Beauty’ in two places:
1. North Border
2. Pond Bed

Reply `1` or `2`.
Ref: GO-A1B2C3
```

A ready proposal should look approximately like:

```text
Ready to save

Plant: Helenium ‘Moerheim Beauty’
Location: North Border
Date: 2026-09-02
Action: Record “Bloomed”
Photo: will be linked to the plant and journal entry

Reply `save` to apply or `cancel` to discard.
Ref: GO-A1B2C3
```

The user may reply to the bot message or include the reference explicitly:

```text
save GO-A1B2C3
cancel GO-A1B2C3
```

A repeated `save` must return the original result and must not create duplicates.

## 4. Explicit non-goals

Do not implement these in the MVP:

- GardenOps web-chat redesign;
- WebMCP;
- a public MCP server;
- MCP OAuth or dynamic client registration;
- multiple Matrix rooms, senders, gardens, or account-linking UI;
- Matrix Application Service registration;
- emoji-reaction approval;
- voice transcription;
- proactive notifications;
- garden-walk batch mode;
- new-plant creation from Matrix;
- plant deletion, record deletion, or other destructive tools;
- vector search or embeddings;
- arbitrary SQL, HTTP, shell, or filesystem tools;
- persistent LLM conversation history;
- multi-agent orchestration;
- a second language/runtime solely for Matrix;
- exact cultivar recognition as a prerequisite for recording an observation.

These may be added after the core workflow has proved useful.

### 4.1 Implemented plant-management extension

The proven core workflow is extended with four explicit, approval-gated plant
actions:

- create a new plant from a photo or name and assign it to a selected plot;
- assign an existing catalog plant to another plot without duplicating it;
- move all or part of a planted quantity between owned plots;
- permanently delete a plant after a proposal clearly states that the action
  cannot be undone.

New plants use the configured AI provider to populate botanical, display, and
care fields, and reuse the existing exact RHS-link resolver. An unverified RHS
match is left blank. User-owned facts such as planting location, planting year,
quantity, and observations are never invented by AI. Every mutation still
requires an explicit `save`, uses the existing GardenOps domain behavior, and
is protected by the existing request idempotency and audit path.

## 5. Runtime architecture

```text
Element / Matrix client
        |
        v
Matrix homeserver
        |
        v
python -m gardenops.matrix_bot
  - Matrix sync and E2EE
  - trigger/command parsing
  - media download
  - temporary capture upload
  - MCP client
  - Matrix response rendering
        |
        |  HTTP upload + MCP Streamable HTTP
        v
GardenOps FastAPI application
  - /api/integrations/matrix/captures
  - /mcp
  - assistant workflow service
  - existing AI provider adapter
  - PlantNet
  - reusable domain command services
        |
        v
PostgreSQL + existing media storage
```

The Matrix bot is a separate process but remains part of the same Python package and deployment. It does not access GardenOps tables directly and does not receive OpenAI, Anthropic, or PlantNet keys.

## 6. Dependencies

### 6.1 MCP

Use the official MCP Python SDK v2 stable line:

```toml
mcp >= 2, < 3
```

Use `MCPServer`, typed tools, structured results, and Streamable HTTP. Do not use the superseded SSE transport.

Official references to read during implementation:

- https://github.com/modelcontextprotocol/python-sdk
- https://py.sdk.modelcontextprotocol.io/
- https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/run/asgi.md

### 6.2 Matrix

Use `matrix-nio` and its asyncio client. The deployment must support both unencrypted and encrypted Matrix rooms.

Keep Matrix dependencies in an optional runtime extra if that avoids imposing `libolm` on installations that do not enable Matrix. Update `uv.lock` normally.

For encrypted rooms:

- install the `e2e` extra and system `libolm` dependency;
- use a persistent crypto store directory;
- use a supplied access token and device ID;
- document manual verification of the bot device from Element;
- do not implement SSO, login UI, cross-signing bootstrap, secret storage, or key-backup management.

Official references:

- https://github.com/matrix-nio/matrix-nio
- https://matrix-nio.readthedocs.io/
- https://spec.matrix.org/latest/client-server-api/

## 7. Configuration

Add these variables to `.env.example`, `ENVIRONMENT_VARIABLES.md`, and the appropriate configuration documentation.

```bash
# MCP server
MCP_ENABLED=false
MCP_BEARER_TOKEN=
MCP_URL=http://127.0.0.1:8000/mcp

# Matrix worker
MATRIX_ENABLED=false
MATRIX_HOMESERVER_URL=
MATRIX_USER_ID=
MATRIX_ACCESS_TOKEN=
MATRIX_DEVICE_ID=
MATRIX_STORE_PATH=/opt/gardenops/matrix
MATRIX_E2EE=true

# Initial single-room binding
MATRIX_ROOM_ID=
MATRIX_ALLOWED_SENDER=
MATRIX_GARDENOPS_USERNAME=
MATRIX_GARDEN_SLUG=
MATRIX_TRIGGER_MODE=mention
MATRIX_TIMEZONE=Europe/Oslo

# Limits
MATRIX_CAPTURE_TTL_DAYS=7
MATRIX_SYNC_TIMEOUT_MS=30000
MATRIX_MAX_PENDING_EVENTS=20
```

Rules:

- `MCP_ENABLED=true` requires a non-placeholder `MCP_BEARER_TOKEN` of at least 32 random characters.
- `MATRIX_ENABLED=true` requires all Matrix credentials, the binding fields, and MCP configuration.
- Resolve the configured GardenOps username and garden slug at worker/server startup and verify active membership plus write access.
- `MATRIX_ALLOWED_SENDER` must be an exact Matrix user ID.
- `MATRIX_ROOM_ID` must be an exact room ID, not a room alias.
- `MATRIX_TRIGGER_MODE` accepts only `mention` or `all`.
- Secrets must not be logged or exposed in status responses.

Do not build a Matrix settings screen in this implementation.

## 8. MCP server integration

Create a module such as:

```text
gardenops/mcp_server.py
```

Use the official SDK's ASGI mounting pattern.

Requirements:

1. Mount the MCP application so clients connect to exactly `/mcp`.
2. Use Streamable HTTP in stateless mode unless the current SDK requires otherwise.
3. Wrap the MCP ASGI app in a tiny static bearer-token middleware using constant-time comparison.
4. Retain the SDK's localhost Host/Origin transport protection.
5. Do not enable browser CORS for MCP.
6. Integrate `mcp.session_manager.run()` into the existing top-level FastAPI lifespan. A mounted sub-application's lifespan does not run automatically.
7. Do not expose `/mcp` through the production nginx example. Add an explicit deny/no-proxy rule before broader locations.
8. MCP handlers must be thin wrappers around the assistant service. They must not contain domain SQL.
9. Because `/mcp` is outside the existing `/api` route gate, every MCP tool must explicitly enforce `MCP_ENABLED`, the configured GardenOps binding, membership/role, and the existing AI feature entitlement before doing work.
10. `assistant_apply` must emit the normal GardenOps mutation audit record explicitly because the existing `/api` mutation middleware does not wrap `/mcp`.
11. Tool output must be structured Pydantic data. Do not rely on parsing prose returned by tools.
12. Add an MCP Inspector smoke-test command to the documentation.

## 9. MCP tool surface

Keep the first tool surface deliberately small. Do not expose every GardenOps table or REST route.

### 9.1 `assistant_process_text`

Purpose: process a new Matrix text message as a question or proposed garden action.

Input:

```json
{
  "source_room_id": "!room:example.org",
  "source_event_id": "$event",
  "source_sender_id": "@user:example.org",
  "text": "I pruned the blackcurrants today",
  "occurred_on": "2026-09-02"
}
```

### 9.2 `assistant_analyze_capture`

Purpose: process a previously uploaded Matrix image plus its caption.

Input:

```json
{
  "source_room_id": "!room:example.org",
  "source_event_id": "$event",
  "source_sender_id": "@user:example.org",
  "capture_asset_id": "media_...",
  "caption": "This has started flowering",
  "occurred_on": "2026-09-02"
}
```

### 9.3 `assistant_continue`

Purpose: answer a clarification question or edit a pending proposal.

Input:

```json
{
  "request_id": "asst_...",
  "source_event_id": "$reply-event",
  "text": "1"
}
```

### 9.4 `assistant_get`

Purpose: retrieve the current request/proposal state for retry or rendering.

Input:

```json
{
  "request_id": "asst_..."
}
```

### 9.5 `assistant_apply`

Purpose: atomically apply a ready proposal after the Matrix worker has parsed an explicit `save` from the allowed sender.

Input:

```json
{
  "request_id": "asst_...",
  "source_event_id": "$save-event"
}
```

### 9.6 `assistant_cancel`

Purpose: cancel a pending request or proposal.

Input:

```json
{
  "request_id": "asst_...",
  "source_event_id": "$cancel-event"
}
```

### 9.7 Common result contract

Every tool returns the same top-level shape:

```json
{
  "state": "answer|needs_input|proposal|applied|cancelled|error",
  "request_id": "asst_...",
  "reference": "GO-A1B2C3",
  "message": "Human-readable Matrix response",
  "choices": [
    {
      "value": "plot_123",
      "label": "North Border",
      "description": ""
    }
  ],
  "proposal": {
    "kind": "journal|harvest|issue|task_completion",
    "summary": "Record Helenium as bloomed in North Border",
    "fields": {}
  },
  "records": [
    {
      "type": "journal_entry",
      "id": "jrn_...",
      "label": "Bloom observation"
    }
  ],
  "retryable": false
}
```

Use an empty list/object instead of omitting collection fields. Validate all output with a strict Pydantic model before returning it through MCP.

## 10. Internal Matrix capture upload

MCP tool inputs are JSON and should not carry multi-megabyte base64 photos. Add one internal binary endpoint:

```text
POST /api/integrations/matrix/captures
```

Request:

- raw image body;
- `Content-Type` set to the Matrix media MIME type;
- `Authorization: Bearer <MCP_BEARER_TOKEN>`;
- `X-Matrix-Room-Id`;
- `X-Matrix-Event-Id`;
- `X-Matrix-Sender`;
- optional `X-Original-Filename`.

Response:

```json
{
  "capture_asset_id": "media_..."
}
```

Requirements:

1. Authenticate using the MCP bearer token, not `AUTH_API_KEY` and not a browser session. Carve this one route out of normal session/CSRF authentication only after the dedicated integration-token check; do not create a general unauthenticated `/api/integrations` bypass.
2. Require the configured room and sender exactly.
3. Resolve the configured GardenOps user and garden server-side.
4. Reuse existing media validation, MIME checking, pixel limits, quota accounting, preview generation, atomic writes, and cleanup machinery.
5. Apply `MAX_AI_PHOTO_BODY_BYTES` or the stricter existing media limit.
6. Make upload idempotent by Matrix room/event ID. A retry returns the existing asset ID.
7. Store the asset in `media_assets` and create a temporary `media_links` row:

```text
target_type = matrix_capture
target_id   = <Matrix event ID>
```

8. Do not add `matrix_capture` to the public media-upload API's accepted target types.
9. Strip EXIF by following the existing image processing behavior.
10. Never log media bytes, encrypted Matrix URLs, access tokens, or full provider prompts.

On successful proposal application, add normal media links to the created record and plant, then remove the temporary `matrix_capture` link. On cancellation or expiry, remove the temporary asset only when it has no durable links.

## 11. Persistence

Create the next sequential migration. If no newer migration exists, use:

```text
migrations/0034_matrix_mcp_assistant.sql
```

Add exactly one new durable table, tentatively named `assistant_requests`.

Suggested schema:

```sql
CREATE TABLE assistant_requests (
    public_id text PRIMARY KEY,
    garden_id bigint NOT NULL REFERENCES gardens(id) ON DELETE CASCADE,
    actor_user_id bigint NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    source_channel text NOT NULL DEFAULT 'matrix',
    source_room_id text NOT NULL,
    source_event_id text NOT NULL,
    source_sender_id text NOT NULL,
    request_kind text NOT NULL,
    state text NOT NULL,
    input_text text NOT NULL DEFAULT '',
    capture_asset_id text REFERENCES media_assets(asset_id) ON DELETE SET NULL,
    payload_json text NOT NULL DEFAULT '{}',
    result_json text NOT NULL DEFAULT '{}',
    error_detail text NOT NULL DEFAULT '',
    created_at_ms bigint NOT NULL,
    updated_at_ms bigint NOT NULL,
    expires_at_ms bigint NOT NULL,
    applied_at_ms bigint,
    last_source_event_id text NOT NULL DEFAULT '',
    UNIQUE (source_channel, source_room_id, source_event_id)
);
```

Add checks or application validation for:

```text
request_kind = question | journal | harvest | issue | task_completion | unknown
state        = processing | needs_input | proposal | answered | applied | cancelled | expired | failed
source_channel = matrix
```

Add only these indexes unless query evidence requires more:

```text
(state, expires_at_ms)
(garden_id, created_at_ms)
```

Update the repository's schema signature/integrity expectations using its existing pattern.

Do not add session, message, tool-call, trace, embedding, vector, queue, or audit-shadow tables.

## 12. Assistant workflow service

Create a small service module or package, for example:

```text
gardenops/services/assistant.py
gardenops/services/assistant_models.py
```

Do not scatter workflow logic through MCP decorators or the Matrix worker.

### 12.1 State machine

A request follows this state machine:

```text
new event
  -> processing
  -> answered                         # garden question
  -> needs_input -> processing        # clarification/edit
  -> proposal -> applied              # explicit save
  -> proposal -> cancelled
  -> failed
  -> expired
```

Each transition must be validated. `assistant_apply` only accepts `proposal`. `assistant_continue` only accepts `needs_input` or `proposal`. `assistant_cancel` is idempotent.

### 12.2 Text interpretation

Add a provider-neutral structured function behind the existing adapter, such as:

```python
interpret_garden_message_with_ai(text: str, context: str, today: str) -> AssistantIntent
```

The structured intent should contain only fields needed by the supported actions:

```text
intent
confidence
plant_query
plot_query
occurred_on
event_type
title
notes
quantity
unit
quality
issue_type
severity
symptoms
task_query
```

Allowed intent values:

```text
question
journal
harvest
issue
task_completion
unknown
```

Validate enums, lengths, dates, quantities, and confidence after provider output. Do not store provider chain-of-thought. A short user-visible explanation is allowed but not required.

### 12.3 Image analysis

Extract the reusable plant-identification logic currently embedded in `gardenops/routers/ai.py` into a service callable by both the existing HTTP route and the assistant.

Add a provider-neutral capture-analysis function that returns:

```text
plant candidates
observed event candidate
health/issue candidate
confidence per independent field
whether another image or user clarification is required
```

Rules:

- Preserve PlantNet as primary identity provider when configured.
- Preserve configured OpenAI/Anthropic fallback and current provider budgets.
- Keep identity confidence separate from event confidence.
- A visible flower may justify `bloomed` even when cultivar identity is uncertain.
- Diagnosis remains advisory until the user saves an issue proposal.
- Never create records from the analysis function.

### 12.4 Garden resolution

Use deterministic SQL and normalization, not LLM judgment, to resolve GardenOps records.

Resolution order:

1. exact normalized Latin name;
2. current external taxonomy references where available;
3. exact normalized common name;
4. unambiguous partial name match;
5. ask the user.

Return one of:

```text
resolved
ambiguous_plant
ambiguous_location
not_found
```

When one plant has several current placements, always ask for location unless the message contains an exact plot match.

Choices must include:

```text
plant public ID
plant display name
Latin name
plot public ID
plot display label / zone
```

Do not add fuzzy-search infrastructure or embeddings.

### 12.5 Question answering

Refactor the existing garden-chat route so its core answer function can be called without an HTTP `Request`.

- Preserve provider selection, timeout, rate/budget accounting, and error semantics.
- Remove or derive the currently hardcoded property-dimension sentence in `build_garden_context`; do not send false fixed dimensions for every garden.
- Matrix Q&A may be one-turn in the MVP. Persistent conversational memory is not required.

## 13. Proposal payloads

Store versioned, typed payload JSON. Start every payload with:

```json
{
  "schema_version": 1
}
```

### 13.1 Journal proposal

```json
{
  "schema_version": 1,
  "event_type": "bloomed",
  "occurred_on": "2026-09-02",
  "title": "First bloom observed",
  "notes": "Recorded from Matrix",
  "plant_ids": ["PLT-..."],
  "plot_ids": ["plot_..."],
  "metadata": {
    "source": "matrix_assistant",
    "matrix_event_id": "$event"
  }
}
```

### 13.2 Harvest proposal

Use the existing harvest fields exactly:

```text
occurred_on
quantity
unit
quality
notes
plant_ids
plot_ids
```

### 13.3 Issue proposal

Use the existing issue fields exactly:

```text
issue_type
title
description
severity
suspected_cause
treatment_plan
follow_up_on
plant_ids
plot_ids
```

Do not invent a separate AI issue type or AI diagnosis table.

### 13.4 Task-completion proposal

Store:

```text
task public ID
expected updated-at revision
selected plant IDs
selected plot IDs
completion outcome required by the existing task action
```

Apply the same conflict behavior as the normal task action endpoint.

## 14. Reusable domain commands

MCP must not call router functions and must not reproduce their SQL.

Perform the smallest necessary refactor of the existing create/action paths into request-independent command functions accepting a database connection and `AuthContext`.

Required command coverage:

```text
create journal entry
create harvest entry
create issue
complete task
link an existing media asset to targets
```

The existing REST routes must call the same command functions after their HTTP validation and offline-idempotency handling.

Preserve all current behavior, including:

- journal plant/plot authorization;
- bloom seen-growing updates;
- harvest-linked journal entry;
- harvest rollups and automations;
- issue notifications and follow-up automation;
- task-completion history and grouped-plant semantics;
- audit behavior;
- media quotas, covers, previews, and cleanup.

Command functions must not commit independently. The caller controls the transaction so record creation, proposal-state update, final media links, and removal of the temporary capture link are atomic.

Do not refactor unrelated update/delete routes.

## 15. Applying a proposal

`assistant_apply` must execute this sequence in one database transaction:

1. Resolve the configured Matrix/GardenOps binding.
2. Lock the `assistant_requests` row with `SELECT ... FOR UPDATE`.
3. If already `applied`, return `result_json` unchanged.
4. Reject cancelled, failed, expired, or non-proposal rows.
5. Verify the allowed sender, room, active GardenOps user, garden membership, and write access again.
6. Revalidate all referenced plants, plots, tasks, and records in the configured garden.
7. Execute the matching reusable domain command.
8. Add media links with `ON CONFLICT DO NOTHING`:
   - journal: journal entry and plant;
   - harvest: harvest entry, linked journal entry when available, and plant;
   - issue: issue and plant;
   - task completion: generated journal entry when available and plant.
9. Remove the temporary `matrix_capture` link only after durable links exist.
10. Write the existing GardenOps mutation audit event with the configured actor, garden, request/proposal ID, Matrix event ID, and created record references. Do not invent a second audit table.
11. Set state to `applied`, store result record references, and set `applied_at_ms`.
12. Commit.

A provider or Matrix failure after this point must not cause the action to run again. Returning `result_json` for subsequent saves is mandatory.

## 16. Matrix worker

Create a dedicated entry point, for example:

```text
gardenops/matrix_bot.py
python -m gardenops.matrix_bot
```

### 16.1 Startup

The worker must:

1. validate Matrix and MCP configuration;
2. initialize a persistent Matrix store;
3. create the MCP client using `MCP_URL` and bearer token;
4. verify GardenOps/MCP availability with bounded retry/backoff;
5. perform an initial sync that establishes the next-batch token without processing old room history;
6. start continuous sync;
7. process events from the configured room sequentially through a bounded `asyncio.Queue`;
8. shut down cleanly on SIGTERM/SIGINT.

Do not process multiple events concurrently for the one configured room. Sequential handling makes replies and proposal state predictable.

### 16.2 Accepted Matrix events

Handle:

```text
m.room.message / m.text
m.room.message / m.image
m.room.encrypted events decrypted by matrix-nio
```

Ignore:

- events sent by the bot itself;
- messages from any other room;
- messages from any other sender;
- edits/redactions in the MVP;
- audio, video, file, sticker, location, and reaction events;
- historical events returned during initial synchronization.

### 16.3 Trigger rules

In `mention` mode, process a new request only when:

- the body starts with `!garden`; or
- the bot is explicitly mentioned; or
- the message is a reply to a GardenOps clarification/proposal message.

In `all` mode, process every accepted sender message in the configured room.

Strip the trigger from the text before sending it to GardenOps.

### 16.4 Command parsing

Parse these deterministically before any AI call:

```text
save [GO-CODE]
cancel [GO-CODE]
<number>                  # reply to a choice message
edit <free text>          # reply to a proposal
help
status
```

Use the reply relation to locate the referenced bot message and extract its visible `GO-XXXXXX` reference. If there is no reply relation, require the reference in the command.

Do not use the LLM to decide whether a user approved a write.

### 16.5 Media handling

For an image event:

1. download and decrypt with matrix-nio;
2. enforce a client-side size check before upload;
3. upload the bytes to `/api/integrations/matrix/captures`;
4. call `assistant_analyze_capture` with the returned asset ID;
5. render the result in Matrix.

Do not give GardenOps a Matrix media URL or Matrix access token.

### 16.6 Rendering

Render plain Matrix text with minimal formatting. Every needs-input or proposal message must end with:

```text
Ref: GO-XXXXXX
```

Use numbered choices. Do not require custom widgets, buttons, reactions, or web links.

On success, return the created GardenOps record types and public IDs. A web link is optional only if GardenOps already has a stable route for that record.

## 17. Maintenance and cleanup

Add a small maintenance function invoked at Matrix-worker startup and once every 24 hours.

It should:

1. mark pending requests past `expires_at_ms` as `expired`;
2. find expired/cancelled/failed requests with a temporary capture;
3. remove the `matrix_capture` link;
4. delete the media asset through existing cleanup machinery only when no other media links remain.

Do not add a new scheduler framework. A simple async periodic task in the Matrix worker is sufficient.

## 18. Security and reliability constraints

This is a local integration, so keep controls proportional, but do not omit the following correctness boundaries:

- MCP uses a dedicated token, never `AUTH_API_KEY`.
- MCP and capture upload accept only the configured local integration.
- Garden/user identity is resolved from server configuration, never trusted from model output.
- All IDs returned by AI are treated as untrusted suggestions and resolved again in the configured garden.
- No garden write occurs from an analysis function.
- Only the deterministic `save` parser may call `assistant_apply`.
- Every apply is transactional and idempotent.
- Prompt content, notes, captions, and text visible in images cannot change the tool list or approval policy.
- Do not expose arbitrary URLs or web-fetch tools to the model.
- Do not log secrets, media bytes, provider payloads, or complete prompts.
- Bound message length, provider output, image size, choice count, and number of candidates.
- Return provider timeout/rate-limit errors to Matrix without creating or changing garden records.
- The worker must recover from Matrix or MCP disconnects with bounded exponential backoff.

## 19. Testing

Add focused tests rather than a large new framework.

Suggested files:

```text
tests/test_mcp_assistant.py
tests/test_assistant_service.py
tests/test_matrix_capture.py
tests/test_matrix_bot.py
tests/test_assistant_actions.py
```

### 19.1 MCP tests

- MCP disabled does not mount or expose tools.
- Missing/invalid bearer token is rejected.
- Tool schemas are discoverable through the SDK test client.
- Every tool returns the common strict result contract.
- Mounted server lifespan starts the MCP session manager.

### 19.2 Request/state tests

- New source room/event is idempotent.
- Duplicate processing returns the existing result.
- Invalid state transitions fail.
- Expired/cancelled request cannot be applied.
- Repeated apply returns the original records.
- Cross-room or cross-sender continuation/apply is rejected.

### 19.3 Resolution tests

- exact Latin match;
- exact common-name match;
- one plant in one location;
- one plant in several locations returns numbered choices;
- several plants return choices;
- no match returns a useful needs-input result;
- plant or plot outside the configured garden never appears.

### 19.4 Action tests

- saved bloom creates one journal entry and updates seen-growing state;
- saved harvest creates one harvest and its linked journal entry;
- saved issue preserves issue automation/notification behavior;
- saved task completion preserves task journal/history behavior;
- photo links move from `matrix_capture` to durable record and plant links;
- failed transaction leaves proposal and domain records unchanged;
- repeated save creates no duplicate media links or records.

### 19.5 Matrix tests

Use fake matrix-nio events and mocked Matrix/MCP clients. Do not connect to a real homeserver.

- room and sender allowlists;
- mention and `!garden` triggers;
- initial sync ignores backlog;
- bot ignores its own events;
- text, image, save, cancel, numbered choice, and edit routing;
- request-reference extraction from a reply;
- queue bound and sequential processing;
- useful messages for provider, MCP, media, and Matrix failures.

### 19.6 Provider tests

Extend the existing deterministic provider. Automated tests must not call real OpenAI, Anthropic, PlantNet, or Matrix endpoints.

### 19.7 Regression checks

Run, at minimum:

```bash
uv run ruff check gardenops tests
uv run ty check gardenops
uv run pytest tests/test_ai_provider.py tests/test_identify.py tests/test_media.py \
  tests/test_journal.py tests/test_harvest.py tests/test_issues.py tests/test_tasks.py \
  tests/test_mcp_assistant.py tests/test_assistant_service.py \
  tests/test_matrix_capture.py tests/test_matrix_bot.py tests/test_assistant_actions.py
cd frontend && npm run typecheck && npm run build
```

Then run the repository's disposable PostgreSQL suite according to current documentation. Never point tests at the live database.

## 20. Deployment

Add:

```text
deploy/gardenops-matrix.service.example
```

Expected shape:

```ini
[Unit]
Description=GardenOps Matrix assistant
After=network-online.target gardenops.service
Wants=network-online.target
Requires=gardenops.service

[Service]
Type=simple
User=gardenops
Group=gardenops
WorkingDirectory=/srv/gardenops/current
EnvironmentFile=/etc/gardenops.env
ExecStart=/srv/gardenops/current/.venv/bin/python -m gardenops.matrix_bot
Restart=always
RestartSec=5
UMask=0077

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/gardenops/matrix /opt/gardenops/media_uploads /opt/gardenops/logs

[Install]
WantedBy=multi-user.target
```

Align hardening and paths with the existing GardenOps service template rather than copying the example blindly.

Document setup:

1. create a dedicated Matrix bot account;
2. obtain an access token and device ID without committing them;
3. invite the bot to the chosen room;
4. verify its device manually when E2EE is enabled;
5. configure the exact room, sender, GardenOps username, and garden slug;
6. generate an MCP token;
7. migrate and restart GardenOps;
8. verify MCP locally;
9. start the Matrix worker;
10. run a non-mutating `!garden status` smoke check;
11. test one disposable observation and delete it manually after verification if desired.

## 21. Implementation sequence

Implement in this order. Keep each step passing before moving on.

### Step 1 — domain seams

- Extract the minimal request-independent command functions.
- Keep existing REST behavior and tests green.

### Step 2 — persistence and temporary media

- Add `assistant_requests` migration and schema expectations.
- Add internal capture upload and cleanup.
- Test idempotent capture ingestion.

### Step 3 — assistant workflow

- Add typed intent/capture schemas.
- Refactor reusable identification and garden-chat service functions.
- Add deterministic plant/location resolution.
- Implement request state transitions and proposals.

### Step 4 — MCP

- Add official MCP SDK.
- Mount local Streamable HTTP server with bearer protection and lifespan integration.
- Implement the six tools and SDK-level tests.

### Step 5 — Matrix

- Add matrix-nio worker, configuration, sync, trigger parsing, image upload, MCP calls, and rendering.
- Add mocked Matrix tests and systemd example.

### Step 6 — complete action coverage

- Verify journal, harvest, issue, and task-completion proposals end to end through the same state machine.
- Verify media links and repeated-save idempotency.

### Step 7 — documentation and full checks

- Update environment/configuration/deployment docs.
- Run targeted checks, disposable PostgreSQL tests, frontend build, and diff/secret review.

## 22. Definition of done

The feature is done when all of the following are true:

- GardenOps exposes an authenticated local MCP endpoint at `/mcp`.
- The MCP endpoint is not available through the production nginx configuration.
- A separate Matrix worker can connect to an encrypted or unencrypted configured room.
- Unauthorized rooms and senders are ignored.
- `!garden` questions receive garden-aware answers.
- A flower photo can be identified, matched, location-clarified, proposed, and saved entirely from Matrix.
- The saved bloom uses the normal journal path, updates seen-growing state, and links the photo.
- Harvest, issue, and task-completion proposals can likewise be completed from Matrix.
- No durable garden write occurs before an explicit `save`.
- Duplicate Matrix delivery, duplicate upload, and duplicate `save` do not create duplicate records.
- Provider failures leave garden records unchanged and produce useful Matrix errors.
- Existing GardenOps REST workflows still pass their tests.
- Automated tests use deterministic or mocked providers and no live database/services.
- Documentation contains a complete local setup and troubleshooting path.

## 23. Coding-agent execution instructions

Implement this specification rather than producing another architecture proposal.

Before changing code:

1. read `AGENTS.md`, `README.md`, `docs/development.md`, and `docs/ai-provider-plan.md`;
2. inspect `git status --short --branch`;
3. inspect the current implementations in:

```text
gardenops/main.py
gardenops/routers/ai.py
gardenops/services/ai_provider.py
gardenops/services/plantnet.py
gardenops/routers/journal.py
gardenops/routers/harvest.py
gardenops/routers/issues.py
gardenops/routers/tasks.py
gardenops/routers/media.py
gardenops/services/observation_updates.py
gardenops/offline_idempotency.py
gardenops/schema_signature.py
```

Implementation constraints:

- Prefer small extraction refactors over rewrites.
- Do not introduce a generic agent framework.
- Do not create extra tables or services unless a demonstrated requirement in this specification cannot be met otherwise.
- Do not silently weaken existing authorization, audit, quota, media, task, issue, or observation behavior.
- Keep provider-specific code behind `gardenops/services/ai_provider.py`.
- Keep Matrix-specific code outside core domain services.
- Keep MCP decorators thin.
- Preserve unrelated changes.
- Update the smallest relevant documentation alongside behavior changes.
- Report exactly which tests and checks were executed and their results.
- Do not deploy, alter production secrets, or run migrations against a live database without explicit user instruction.

When implementation details differ because the repository has changed, choose the smallest design that preserves the decisions and acceptance criteria in this document, and record the deviation in the final implementation summary.
