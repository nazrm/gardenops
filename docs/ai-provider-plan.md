# AI Provider Integration

GardenOps uses the official OpenAI SDK through
`gardenops/services/ai_provider.py`. Browser chat, plant lookup, image
identification and diagnosis, care generation and task descriptions share this
adapter. PlantNet identification remains independently available.

- `AI_PROVIDER` accepts `openai` or `disabled`; unset means disabled.
- Configure `OPENAI_API_KEY`, `OPENAI_MODEL` and optionally `OPENAI_FAST_MODEL`
  through host settings or the platform-admin provider settings UI.
- Database-managed keys remain encrypted, admin-only and redacted in responses.
- Unknown or retired provider selections normalize to disabled. There is no
  automatic switch to a paid provider and no cross-provider fallback.
- Structured output schemas, timeouts, budgets and deterministic fallbacks stay
  in the existing adapter; do not replace the SDK with a custom HTTP client.
- External assistant transports are no longer included. Applied database
  migrations are unchanged and historical private captures remain unreadable.

Validation uses mocked SDK unit tests, the loopback OpenAI-compatible fixture
and disposable browser journeys. No production credentials or paid calls are
required. Historical plans and secret-redaction tests may reference retired
providers; they do not enable integrations.
