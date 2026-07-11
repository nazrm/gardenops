# AI Provider Implementation Plan

This is the implementation spec for moving GardenOps AI features from
hardcoded Anthropic calls to a configured provider model while preserving
PlantNet as the primary plant-identification provider.

Current deployments can manage OpenAI and Anthropic keys from the platform
admin UI. `AI_PROVIDER` and provider key environment variables remain supported
as fallback configuration, but the env-only setup described below is superseded
by admin-managed provider settings when `APP_SECRETS_ENCRYPTION_KEY` is set.

## Original Problem

- `gardenops/routers/ai.py` hardcoded `claude-sonnet-4-20250514` in multiple
  Anthropic calls.
- `identify-plant` used PlantNet first, then Claude vision as a fallback when
  PlantNet failed or returned low confidence.
- `diagnose-plant` used Claude vision directly.
- Other AI features used Anthropic directly:
  - plant lookup
  - care generation
  - garden chat
- `gardenops/services/task_generator.py` used Anthropic directly for generated
  task text.
- The repo already had `ANTHROPIC_API_KEY` and `PLANTNET_API_KEY` references;
  docs mentioned `OPENAI_API_KEY`, but OpenAI was not implemented.

## Target State

- `AI_PROVIDER=anthropic|openai` controls every LLM-backed feature.
- `identify-plant` always tries PlantNet first when `PLANTNET_API_KEY` is
  configured.
- `identify-plant` uses the configured `AI_PROVIDER` only as fallback when
  PlantNet fails, returns no candidates, or top confidence is below threshold.
- `diagnose-plant` uses the configured `AI_PROVIDER`.
- Plant lookup, care generation, garden chat, and task text generation use the
  configured `AI_PROVIDER`.
- Provider-specific code lives behind a small adapter boundary. Router and
  service code should not directly instantiate Anthropic or OpenAI clients.
- Provider calls in unit tests are mocked. A separate browser journey may use
  the local deterministic fixture provider, which is available only when
  `APP_ENV=test` and its explicit E2E flag are both set. Tests must not call real
  OpenAI, Anthropic, PlantNet, or live databases.

## Non-Goals

- Do not change PlantNet request behavior except for the fallback handoff.
- Do not run any migration, test, or validation against the live database.
- Do not add automatic provider fallback from Anthropic to OpenAI or OpenAI to
  Anthropic. The chosen provider is explicit.
- Do not change user-facing AI workflows beyond provider routing, model
  configuration, and clearer provider-unavailable errors.
- Do not store provider responses beyond existing behavior.

## Provider Matrix

| Feature | Provider behavior |
|---|---|
| `POST /api/ai/identify-plant` | PlantNet primary, configured `AI_PROVIDER` fallback |
| `POST /api/ai/diagnose-plant` | configured `AI_PROVIDER` |
| plant lookup | configured `AI_PROVIDER` |
| care generation | configured `AI_PROVIDER` |
| garden chat | configured `AI_PROVIDER` |
| task text generation | configured `AI_PROVIDER` |

## Configuration

Add one explicit provider selector and provider-specific model knobs.

```bash
# Leave AI_PROVIDER unset to disable LLM-backed AI features.
# AI_PROVIDER=anthropic

ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-4-6

OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.5
OPENAI_FAST_MODEL=gpt-5.4-mini

PLANTNET_API_KEY=
PLANTNET_CONFIDENCE_THRESHOLD=0.40
```

Rules:

- `AI_PROVIDER` must be normalized to lowercase and accept only `anthropic` or
  `openai`.
- If `AI_PROVIDER` is unset, LLM-backed AI features are disabled. The app must
  not infer a provider from available API keys.
- If `AI_PROVIDER=anthropic`, require `ANTHROPIC_API_KEY` for LLM-backed
  features.
- If `AI_PROVIDER=openai`, require `OPENAI_API_KEY` for LLM-backed features.
- `ANTHROPIC_MODEL` defaults to `claude-sonnet-4-6`.
- `OPENAI_MODEL` defaults to `gpt-5.5` as of the 2026-05-19 docs check; recheck
  official docs during implementation before committing the model default.
- `OPENAI_FAST_MODEL` is optional and can be used later for low-risk text tasks,
  but initial implementation should prefer one model path unless a feature
  already has a clear fast/quality split.
- `PLANTNET_API_KEY` is independent of `AI_PROVIDER`.
- `PLANTNET_CONFIDENCE_THRESHOLD` remains the threshold for deciding whether
  PlantNet is confident enough to skip LLM fallback.

## Adapter Boundary

Add an adapter module, for example `gardenops/services/ai_provider.py`.

The adapter should expose typed functions that match GardenOps use cases, not
raw provider SDK methods:

```python
identify_plant_with_ai(image_bytes, organ) -> list[PlantCandidate]
diagnose_plant_with_ai(image_bytes, prompt_text) -> list[PlantDiagnosis]
lookup_plant_with_ai(prompt_text) -> PlantLookupResult
generate_care_with_ai(prompt_text) -> CareGenerationResult
chat_with_ai(messages, context) -> GardenChatResult
generate_task_text_with_ai(prompt_text) -> TaskTextResult
```

Implementation rules:

- The adapter resolves `AI_PROVIDER` once per call.
- The adapter creates Anthropic or OpenAI clients through provider-specific
  helpers.
- The adapter maps provider output into internal typed results.
- The adapter raises a local exception for missing provider configuration, for
  example `AIProviderNotConfigured`.
- The adapter raises a local exception for upstream failures, for example
  `AIProviderError`.
- Routers convert local exceptions to existing HTTP semantics.
- Provider-specific prompts and schemas live beside the adapter, not spread
  across routers.

## Data Contracts

Keep response shapes stable for clients.

Identification candidate:

```json
{
  "name": "string",
  "latin": "string",
  "scientific_name": "string",
  "family": "string",
  "confidence": 0.0,
  "source": "plantnet|anthropic|openai",
  "gbif_id": "string"
}
```

Diagnosis:

```json
{
  "issue_type": "pest|disease|fungal|nutrient|environmental|damage|other",
  "likely_cause": "string",
  "confidence": "high|medium|low",
  "description": "string",
  "suggested_treatment": "string",
  "reasoning": "string",
  "related_history": "string"
}
```

Validation rules:

- Clamp numeric confidence to `0.0..1.0`.
- Truncate free-text fields to current limits.
- Normalize invalid diagnosis enum values to current safe defaults.
- Treat malformed provider responses as upstream failures, not partial success,
  unless there are already valid candidates or diagnoses.
- Preserve existing request body limits and image preprocessing.

## Identification Flow

1. Authenticate and apply existing rate limits.
2. Validate `organ`.
3. Read and preprocess the uploaded image.
4. If `PLANTNET_API_KEY` is configured:
   - reserve provider budget as today
   - call PlantNet
   - map candidates with `source="plantnet"`
   - if top confidence is at or above `PLANTNET_CONFIDENCE_THRESHOLD`, return
     PlantNet candidates
5. If PlantNet is not configured, failed, returned no candidates, or returned
   low confidence:
   - call `identify_plant_with_ai(...)` using configured `AI_PROVIDER`
   - merge AI candidates after PlantNet candidates, deduplicating by lowercase
     Latin name where available
   - mark AI candidate `source` as `anthropic` or `openai`
6. If no provider is configured or all configured providers fail with no usable
   candidates, return `503` or `502` consistently with current upstream-error
   behavior.
7. Sort by confidence and return at most five candidates.

Failure semantics:

- Invalid organ, image type, empty body, or oversized body: existing `4xx`.
- No PlantNet and no configured AI provider key: `503`.
- PlantNet failure plus successful AI fallback: `200`.
- PlantNet low confidence plus successful AI fallback: `200`.
- PlantNet success with high confidence: `200` and no AI call.
- PlantNet failure plus AI upstream failure: `502`.
- AI configured provider missing key: `503`.

## Diagnosis Flow

1. Authenticate and apply existing rate limits.
2. Read and preprocess the uploaded image.
3. Load plant and plot context with the existing authorization checks.
4. Build the diagnosis prompt from existing context.
5. Reserve provider budget as today.
6. Call `diagnose_plant_with_ai(...)` using configured `AI_PROVIDER`.
7. Return the current response shape with `diagnoses`, `context_used`, and
   disclaimer.

Failure semantics:

- Invalid image, empty body, oversized body, or unauthorized context: existing
  behavior.
- Configured AI provider missing or missing key: `503`.
- Configured provider upstream failure: `502`.
- Healthy plant / no diagnosis from provider: `200` with empty `diagnoses`.

## Non-Vision AI Flow

Refactor each existing Anthropic-only text workflow to call the adapter:

- plant lookup
- care generation
- garden chat
- task text generation

Keep feature-specific prompts, truncation, response limits, budget accounting,
rate limits, and error mapping functionally equivalent.

## Provider Implementation Details

Anthropic:

- Use the Messages API through the existing `anthropic` SDK.
- Replace hardcoded model strings with `ANTHROPIC_MODEL`.
- Default model: `claude-sonnet-4-6`.
- Preserve the existing tool-use schemas where they already work.
- Keep image-before-text ordering for vision requests.

OpenAI:

- Add the official `openai` Python SDK dependency.
- Use the Responses API for new OpenAI calls.
- Use image input for identify/diagnose.
- Use structured outputs or equivalent schema-constrained output for structured
  feature responses.
- Represent image bytes as a data URL unless the SDK provides a cleaner local
  file input path that avoids temporary persistent files.
- Parse refusals and malformed structured output into provider errors with
  useful logs but without leaking secrets.

## Security And Operations

- Never log API keys, request bodies, base64 image data, or full provider
  prompts.
- Preserve existing security-event counters and add provider-specific tags where
  useful.
- Keep current payload-size limits.
- Keep current provider budget reservations.
- Add provider name to observability metadata for upstream failures.
- Continue disabling real provider calls in tests.
- Document that LLM diagnosis is advisory and not a definitive plant-health
  diagnosis.

## Rollout Plan

1. Add adapter scaffolding and configuration helpers.
2. Move Anthropic calls behind the adapter while preserving behavior.
3. Add OpenAI dependency and OpenAI adapter paths.
4. Convert `identify-plant` fallback to configured-provider fallback.
5. Convert `diagnose-plant` to configured-provider routing.
6. Convert non-vision AI features to configured-provider routing.
7. Update docs and env examples.
8. Run targeted mocked provider tests.
9. Run lint/format checks.
10. Run the disposable local PostgreSQL suite:
    `scripts/run_fast_postgres_tests.py --full-suite --shards 4`.
11. Verify the live app only with non-mutating health checks. Do not run tests
    against the live database.

## Test Matrix

Identification:

- PlantNet high confidence returns PlantNet results and does not call AI.
- PlantNet low confidence calls Anthropic when `AI_PROVIDER=anthropic`.
- PlantNet low confidence calls OpenAI when `AI_PROVIDER=openai`.
- PlantNet timeout calls configured AI fallback.
- PlantNet invalid response calls configured AI fallback.
- No PlantNet key calls configured AI provider directly.
- No PlantNet key and missing configured AI key returns `503`.
- PlantNet failure and AI failure returns `502`.
- Candidate merge deduplicates by Latin name.
- Candidate sources are preserved.

Diagnosis:

- `AI_PROVIDER=anthropic` calls Anthropic adapter.
- `AI_PROVIDER=openai` calls OpenAI adapter.
- Missing configured key returns `503`.
- Provider failure returns `502`.
- Existing plant/plot authorization tests still pass.
- Healthy/no-diagnosis provider response returns empty `diagnoses`.

Non-vision AI:

- Each feature uses Anthropic when `AI_PROVIDER=anthropic`.
- Each feature uses OpenAI when `AI_PROVIDER=openai`.
- Invalid `AI_PROVIDER` returns a configuration error.
- Configured model env vars are passed to the provider client.
- Existing truncation and schema validation tests still pass.

Regression:

- No real provider SDK network calls occur in unit tests.
- The deterministic-provider browser journey uses real GardenOps routes and
  local budget accounting on desktop and mobile. Its isolated child process
  scrubs inherited provider credentials and proxies, uses the deterministic
  adapter, blocks non-loopback browser traffic before transmission, and scans
  backend logs for vendor credential material.
- Disposable Postgres test runner still completes.
- `npm run build` still passes.
- Environment variable documentation checker still passes.

## Resolved Decisions

- `AI_PROVIDER` has no default. Leaving it unset disables LLM-backed AI features.
- Initial implementation uses `OPENAI_MODEL` for all OpenAI-backed features.
  `OPENAI_FAST_MODEL` is documented and reserved for future fast-path work.
- PlantNet low-confidence results are merged with AI candidates, deduplicated by
  Latin name, then sorted by confidence.
- Diagnosis response shape does not gain a `source` field; provider source stays
  in logs and metrics to avoid client churn.

## Documentation Sources To Recheck During Implementation

- Anthropic model deprecations:
  `https://platform.claude.com/docs/en/about-claude/model-deprecations`
  says `claude-sonnet-4-20250514` retires on 2026-06-15 and lists
  `claude-sonnet-4-6` as the replacement.
- Anthropic vision docs: Claude vision supports image input and recommends
  client-side image resizing to control latency and token use:
  `https://platform.claude.com/docs/en/build-with-claude/vision`.
- OpenAI models docs:
  `https://developers.openai.com/api/docs/models` says current latest OpenAI
  models support text and image input, text output, and vision through the
  Responses API.
- OpenAI structured-output docs:
  `https://developers.openai.com/api/docs/guides/structured-outputs` should be
  used for schema-constrained JSON response contracts.
