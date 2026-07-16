"""Tests for the configured AI provider adapter."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import anthropic
import httpx
import openai

from gardenops.services import ai_provider
from gardenops.services.ai_provider import (
    AIProviderError,
    AIProviderNotConfigured,
    AIProviderRateLimited,
    AIProviderTimeout,
    chat_with_ai,
    configured_provider,
    diagnose_plant_with_ai,
    generate_task_descriptions_with_ai,
    identify_plant_with_ai,
)

_EXPECTED_HEAD = subprocess.check_output(
    ["git", "rev-parse", "--verify", "HEAD"],
    cwd=Path(__file__).resolve().parents[1],
    text=True,
).strip()


def _complete_journey_fixture_env(artifact_dir: Path) -> dict[str, str]:
    """Return the runner-issued contract required for local provider overrides."""
    database_url = "postgresql://gardenops-test@127.0.0.1:19452/gardenops_test"
    return {
        "APP_ENV": "test",
        "DATABASE_URL": database_url,
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_ALLOW_TRUNCATE": "1",
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_ARTIFACT_DIR": str(artifact_dir),
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_CHILD": "1",
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_EXPECTED_HEAD": _EXPECTED_HEAD,
        "GARDENOPS_DISPOSABLE_POSTGRES_MARKER": "123.fixture",
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER": "123",
        "GARDENOPS_DISPOSABLE_POSTGRES_URL": database_url,
        "GARDENOPS_E2E_LOOPBACK_PROVIDER": "1",
        "GARDENOPS_E2E_PROVIDER_URL": "http://127.0.0.1:19451/v1",
    }


class TestAIProviderConfig(unittest.TestCase):
    def test_unset_provider_is_disabled(self) -> None:
        with patch.dict(os.environ, {"AI_PROVIDER": ""}, clear=False):
            with self.assertRaises(AIProviderNotConfigured):
                configured_provider()

    def test_explicit_disabled_provider_reports_not_configured(self) -> None:
        with patch.dict(os.environ, {"AI_PROVIDER": "disabled"}, clear=False):
            with self.assertRaisesRegex(
                AIProviderNotConfigured,
                "^AI provider not configured$",
            ):
                configured_provider()

    def test_invalid_provider_raises_configuration_error(self) -> None:
        with patch.dict(os.environ, {"AI_PROVIDER": "bogus"}, clear=False):
            with self.assertRaises(AIProviderNotConfigured):
                configured_provider()

    def test_loopback_fixture_url_is_test_only_and_strictly_validated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture_env = _complete_journey_fixture_env(Path(tmp))
            with patch.dict(os.environ, fixture_env, clear=False):
                self.assertEqual(
                    ai_provider._loopback_openai_base_url(),
                    "http://127.0.0.1:19451/v1",
                )

            invalid_urls = (
                "https://127.0.0.1:19451/v1",
                "http://localhost:19451/v1",
                "http://127.0.0.1:5432/v1",
                "http://127.0.0.1:19451/not-v1",
                "http://127.0.0.1:not-a-port/v1",
                "http://user:pass@127.0.0.1:19451/v1",
            )
            for value in invalid_urls:
                with (
                    self.subTest(value=value),
                    patch.dict(
                        os.environ,
                        {**fixture_env, "GARDENOPS_E2E_PROVIDER_URL": value},
                        clear=False,
                    ),
                ):
                    with self.assertRaises(AIProviderNotConfigured):
                        ai_provider._loopback_openai_base_url()

            with patch.dict(
                os.environ,
                {**fixture_env, "APP_ENV": "production"},
                clear=False,
            ):
                self.assertIsNone(ai_provider._loopback_openai_base_url())
            with patch.dict(
                os.environ,
                {**fixture_env, "GARDENOPS_COMPLETE_JOURNEYS_E2E_CHILD": ""},
                clear=False,
            ):
                self.assertIsNone(ai_provider._loopback_openai_base_url())


class TestAIProviderAdapter(unittest.TestCase):
    def test_openai_client_uses_loopback_fixture_only_when_opted_in(self) -> None:
        mocked_client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.dict(
                    os.environ,
                    _complete_journey_fixture_env(Path(tmp)),
                    clear=False,
                ),
                patch("gardenops.services.ai_provider.OpenAI", return_value=mocked_client) as ctor,
            ):
                self.assertIs(ai_provider._openai_client("test-key"), mocked_client)

        self.assertEqual(ctor.call_args.kwargs["base_url"], "http://127.0.0.1:19451/v1")

    def test_anthropic_identify_uses_configured_model_and_source(self) -> None:
        response_block = type(
            "ToolBlock",
            (),
            {
                "type": "tool_use",
                "name": "plant_candidates",
                "input": {
                    "candidates": [
                        {
                            "name": "Nyperose",
                            "latin": "Rosa canina",
                            "family": "Rosaceae",
                            "confidence": 0.8,
                            "reasoning": "Visible rose leaves.",
                        }
                    ]
                },
            },
        )()
        mocked_client = MagicMock()
        mocked_client.messages.create.return_value = type(
            "AnthropicResponse",
            (),
            {"content": [response_block]},
        )()

        with (
            patch.dict(
                os.environ,
                {
                    "AI_PROVIDER": "anthropic",
                    "ANTHROPIC_API_KEY": "test-key",
                    "ANTHROPIC_MODEL": "claude-test-model",
                },
                clear=False,
            ),
            patch("gardenops.services.ai_provider.Anthropic", return_value=mocked_client),
        ):
            candidates = identify_plant_with_ai(b"fake-jpeg", "leaf")

        self.assertEqual(candidates[0]["source"], "anthropic")
        self.assertEqual(candidates[0]["latin"], "Rosa canina")
        self.assertEqual(
            mocked_client.messages.create.call_args.kwargs["model"], "claude-test-model"
        )

    def test_openai_identify_uses_responses_api_and_source(self) -> None:
        mocked_client = MagicMock()
        mocked_client.responses.create.return_value = type(
            "OpenAIResponse",
            (),
            {
                "output_text": json.dumps(
                    {
                        "candidates": [
                            {
                                "name": "Nyperose",
                                "latin": "Rosa canina",
                                "family": "Rosaceae",
                                "confidence": 0.78,
                                "reasoning": "Visible rose leaves.",
                            }
                        ]
                    }
                )
            },
        )()

        with (
            patch.dict(
                os.environ,
                {
                    "AI_PROVIDER": "openai",
                    "OPENAI_API_KEY": "test-key",
                    "OPENAI_MODEL": "gpt-test-model",
                },
                clear=False,
            ),
            patch("gardenops.services.ai_provider.OpenAI", return_value=mocked_client),
        ):
            candidates = identify_plant_with_ai(b"fake-jpeg", "leaf")

        call = mocked_client.responses.create.call_args.kwargs
        self.assertEqual(call["model"], "gpt-test-model")
        self.assertEqual(call["text"]["format"]["name"], "plant_candidates")
        self.assertEqual(candidates[0]["source"], "openai")

    def test_openai_diagnose_parses_diagnoses(self) -> None:
        mocked_client = MagicMock()
        mocked_client.responses.create.return_value = type(
            "OpenAIResponse",
            (),
            {
                "output_text": json.dumps(
                    {
                        "diagnoses": [
                            {
                                "issue_type": "fungal",
                                "likely_cause": "Powdery mildew",
                                "confidence": "high",
                                "description": "White powder on leaves.",
                                "suggested_treatment": "Improve airflow.",
                                "reasoning": "Classic mildew symptoms.",
                                "related_history": "",
                            }
                        ]
                    }
                )
            },
        )()

        with (
            patch.dict(
                os.environ,
                {"AI_PROVIDER": "openai", "OPENAI_API_KEY": "test-key"},
                clear=False,
            ),
            patch("gardenops.services.ai_provider.OpenAI", return_value=mocked_client),
        ):
            diagnoses = diagnose_plant_with_ai(b"fake-jpeg", "Diagnose this plant.")

        self.assertEqual(diagnoses[0]["issue_type"], "fungal")
        self.assertEqual(diagnoses[0]["confidence"], "high")

    def test_openai_chat_uses_configured_provider(self) -> None:
        mocked_client = MagicMock()
        mocked_client.responses.create.return_value = type(
            "OpenAIResponse",
            (),
            {"output_text": "Use compost."},
        )()

        with (
            patch.dict(
                os.environ,
                {"AI_PROVIDER": "openai", "OPENAI_API_KEY": "test-key"},
                clear=False,
            ),
            patch("gardenops.services.ai_provider.OpenAI", return_value=mocked_client),
        ):
            reply = chat_with_ai("You are concise.", [{"role": "user", "content": "Tip?"}])

        self.assertEqual(reply, "Use compost.")
        self.assertEqual(
            mocked_client.responses.create.call_args.kwargs["instructions"], "You are concise."
        )

    def test_openai_chat_can_use_fast_model_and_smaller_output_budget(self) -> None:
        mocked_client = MagicMock()
        mocked_client.responses.create.return_value = type(
            "OpenAIResponse",
            (),
            {"output_text": "Mulch now."},
        )()

        with (
            patch.dict(
                os.environ,
                {
                    "AI_PROVIDER": "openai",
                    "OPENAI_API_KEY": "test-key",
                    "OPENAI_MODEL": "gpt-main-test",
                    "OPENAI_FAST_MODEL": "gpt-fast-test",
                },
                clear=False,
            ),
            patch("gardenops.services.ai_provider.OpenAI", return_value=mocked_client),
        ):
            reply = chat_with_ai(
                "You are concise.",
                [{"role": "user", "content": "Tip?"}],
                use_fast_model=True,
                max_tokens=768,
            )

        call = mocked_client.responses.create.call_args.kwargs
        self.assertEqual(reply, "Mulch now.")
        self.assertEqual(call["model"], "gpt-fast-test")
        self.assertEqual(call["max_output_tokens"], 768)

    def test_openai_chat_can_override_provider_timeout(self) -> None:
        mocked_client = MagicMock()
        mocked_client.responses.create.return_value = type(
            "OpenAIResponse",
            (),
            {"output_text": "Mulch now."},
        )()

        with (
            patch.dict(
                os.environ,
                {
                    "AI_PROVIDER": "openai",
                    "OPENAI_API_KEY": "test-key",
                },
                clear=False,
            ),
            patch(
                "gardenops.services.ai_provider.OpenAI",
                return_value=mocked_client,
            ) as mocked_openai,
        ):
            chat_with_ai(
                "You are concise.",
                [{"role": "user", "content": "Tip?"}],
                timeout_seconds=60,
            )

        self.assertEqual(mocked_openai.call_args.kwargs["timeout"], 60.0)

    def test_openai_chat_timeout_raises_timeout_error(self) -> None:
        request = httpx.Request("POST", "https://api.openai.com/v1/responses")
        mocked_client = MagicMock()
        mocked_client.responses.create.side_effect = openai.APITimeoutError(request)

        with (
            patch.dict(
                os.environ,
                {"AI_PROVIDER": "openai", "OPENAI_API_KEY": "test-key"},
                clear=False,
            ),
            patch("gardenops.services.ai_provider.OpenAI", return_value=mocked_client),
        ):
            with self.assertRaises(AIProviderTimeout):
                chat_with_ai("You are concise.", [{"role": "user", "content": "Tip?"}])

    def test_openai_chat_rate_limit_is_classified(self) -> None:
        request = httpx.Request("POST", "https://api.openai.com/v1/responses")
        response = httpx.Response(429, request=request)
        mocked_client = MagicMock()
        mocked_client.responses.create.side_effect = openai.RateLimitError(
            "Fixture quota exhausted.",
            response=response,
            body={"error": {"code": "insufficient_quota"}},
        )

        with (
            patch.dict(
                os.environ,
                {"AI_PROVIDER": "openai", "OPENAI_API_KEY": "test-key"},
                clear=False,
            ),
            patch("gardenops.services.ai_provider.OpenAI", return_value=mocked_client),
        ):
            with self.assertRaises(AIProviderRateLimited):
                chat_with_ai("You are concise.", [{"role": "user", "content": "Tip?"}])

    def test_anthropic_chat_rate_limit_is_classified(self) -> None:
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx.Response(429, request=request)
        mocked_client = MagicMock()
        mocked_client.messages.create.side_effect = anthropic.RateLimitError(
            "Fixture quota exhausted.",
            response=response,
            body={"error": {"type": "rate_limit_error"}},
        )

        with (
            patch.dict(
                os.environ,
                {"AI_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "test-key"},
                clear=False,
            ),
            patch("gardenops.services.ai_provider.Anthropic", return_value=mocked_client),
        ):
            with self.assertRaises(AIProviderRateLimited):
                chat_with_ai("You are concise.", [{"role": "user", "content": "Tip?"}])

    def test_openai_chat_blank_response_is_malformed(self) -> None:
        mocked_client = MagicMock()
        mocked_client.responses.create.return_value = type(
            "OpenAIResponse",
            (),
            {"output_text": "   "},
        )()

        with (
            patch.dict(
                os.environ,
                {"AI_PROVIDER": "openai", "OPENAI_API_KEY": "test-key"},
                clear=False,
            ),
            patch("gardenops.services.ai_provider.OpenAI", return_value=mocked_client),
        ):
            with self.assertRaisesRegex(AIProviderError, "did not return text output"):
                chat_with_ai("You are concise.", [{"role": "user", "content": "Tip?"}])

    def test_openai_identify_timeout_is_classified(self) -> None:
        request = httpx.Request("POST", "https://api.openai.com/v1/responses")
        mocked_client = MagicMock()
        mocked_client.responses.create.side_effect = openai.APITimeoutError(request)

        with (
            patch.dict(
                os.environ,
                {"AI_PROVIDER": "openai", "OPENAI_API_KEY": "test-key"},
                clear=False,
            ),
            patch("gardenops.services.ai_provider.OpenAI", return_value=mocked_client),
        ):
            with self.assertRaises(AIProviderTimeout):
                identify_plant_with_ai(b"fake-jpeg", "leaf")

    def test_anthropic_diagnose_timeout_is_classified(self) -> None:
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        mocked_client = MagicMock()
        mocked_client.messages.create.side_effect = anthropic.APITimeoutError(request)

        with (
            patch.dict(
                os.environ,
                {"AI_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "test-key"},
                clear=False,
            ),
            patch("gardenops.services.ai_provider.Anthropic", return_value=mocked_client),
        ):
            with self.assertRaises(AIProviderTimeout):
                diagnose_plant_with_ai(b"fake-jpeg", "Diagnose this plant.")

    def test_anthropic_chat_timeout_raises_timeout_error(self) -> None:
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        mocked_client = MagicMock()
        mocked_client.messages.create.side_effect = anthropic.APITimeoutError(request)

        with (
            patch.dict(
                os.environ,
                {
                    "AI_PROVIDER": "anthropic",
                    "ANTHROPIC_API_KEY": "test-key",
                },
                clear=False,
            ),
            patch("gardenops.services.ai_provider.Anthropic", return_value=mocked_client),
        ):
            with self.assertRaises(AIProviderTimeout):
                chat_with_ai("You are concise.", [{"role": "user", "content": "Tip?"}])

    def test_openai_task_descriptions_use_configured_provider(self) -> None:
        mocked_client = MagicMock()
        mocked_client.responses.create.return_value = type(
            "OpenAIResponse",
            (),
            {
                "output_text": json.dumps(
                    {
                        "tasks": [
                            {
                                "task_key": "task-1",
                                "description_en": "Prune now.",
                                "description_no": "Beskjaer naa.",
                            }
                        ]
                    }
                )
            },
        )()

        with (
            patch.dict(
                os.environ,
                {"AI_PROVIDER": "openai", "OPENAI_API_KEY": "test-key"},
                clear=False,
            ),
            patch("gardenops.services.ai_provider.OpenAI", return_value=mocked_client),
        ):
            tasks = generate_task_descriptions_with_ai(
                [{"task_key": "task-1"}],
                preferred_locale="no",
            )

        self.assertEqual(tasks[0]["task_key"], "task-1")
        self.assertEqual(
            mocked_client.responses.create.call_args.kwargs["text"]["format"]["name"],
            "task_descriptions_batch",
        )


if __name__ == "__main__":
    unittest.main()
