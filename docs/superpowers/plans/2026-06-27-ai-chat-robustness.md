# AI Chat Robustness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make garden chat resilient to upstream AI latency by using the fast OpenAI model, AI-specific timeouts, clearer timeout errors, telemetry, and e2e validation in one PR.

**Architecture:** Keep normal API behavior unchanged. Add a narrow chat-specific model/timeout contract in `gardenops.services.ai_provider`, map timeout failures to 504 in `gardenops.routers.ai`, and add a frontend garden-chat timeout/error path through the existing API wrapper.

**Tech Stack:** FastAPI, OpenAI/Anthropic Python clients, pytest/unittest, Vite/TypeScript, Playwright.

---

### Task 1: Backend Provider Contract

**Files:**
- Modify: `gardenops/services/ai_provider.py`
- Test: `tests/test_ai_provider.py`

- [x] Add failing tests proving OpenAI garden chat can select `openai_fast_model`, lower max tokens, override provider timeout, and classify OpenAI/Anthropic timeout exceptions as `AIProviderTimeout`.
- [x] Implement `AIProviderTimeout`, provider timeout exception mapping, and `chat_with_ai(..., use_fast_model=True, max_tokens=..., timeout_seconds=...)`.
- [x] Run: `uv run --group test python -m unittest tests.test_ai_provider.TestAIProviderAdapter.test_openai_chat_can_use_fast_model_and_smaller_output_budget tests.test_ai_provider.TestAIProviderAdapter.test_openai_chat_can_override_provider_timeout tests.test_ai_provider.TestAIProviderAdapter.test_openai_chat_timeout_raises_timeout_error tests.test_ai_provider.TestAIProviderAdapter.test_anthropic_chat_timeout_raises_timeout_error -v`.

### Task 2: Garden Chat Route Behavior

**Files:**
- Modify: `gardenops/routers/ai.py`
- Test: `tests/test_plots.py`

- [x] Add failing route tests proving `/api/ai/garden-chat` uses the fast OpenAI model and returns HTTP 504 with a clean detail when the provider times out.
- [x] Implement route call options, duration/context telemetry, and timeout-specific 504 mapping without changing unrelated AI routes.
- [x] Run targeted route tests against a disposable Postgres cluster: `tests/test_plots.py::TestPlots::test_ai_garden_chat_daily_budget_enforced`, `tests/test_plots.py::TestPlots::test_ai_garden_chat_uses_openai_fast_model`, and `tests/test_plots.py::TestPlots::test_ai_garden_chat_provider_timeout_returns_504`.

### Task 3: Frontend Client Contract

**Files:**
- Modify: `frontend/src/services/api.ts`
- Create: `scripts/check_ai_chat_client_contract.cjs`
- Modify: `frontend/package.json`

- [x] Add a failing static contract check proving `gardenChatApi` uses an AI-specific timeout and timeout message.
- [x] Implement `timeoutMessage` support in the shared API wrapper and pass the garden-chat timeout/message from `gardenChatApi`.
- [x] Include the contract check in `npm run build`.
- [x] Run: `cd frontend && npm run typecheck && npm run build`.

### Task 4: E2E Validation

**Files:**
- No committed e2e artifact required.

- [x] Start a local backend from this branch with disposable Postgres, seeded pro test user, and test provider settings.
- [x] Serve the built frontend from this branch through the same FastAPI process.
- [x] Use Playwright/Chromium to log in and run the authenticated chat flow with `chat_with_ai` monkeypatched to raise `AIProviderTimeout`.
- [x] Prove the UI remains loaded, shows `AI provider request timed out`, receives a 504 chat response, and re-enables the chat controls.

### Task 5: Docs, Sanitizer, PR

**Files:**
- Modify docs only if the docs impact inventory flags required public documentation changes.

- [x] Run docs impact inventory and update docs or record why docs are unchanged.
- [x] Run git push sanitizer before staging/commit/push.
- [x] Run focused backend/frontend checks and e2e again after implementation.
- [x] Commit all scoped changes, push `codex/ai-chat-robustness`, and open one draft PR.
