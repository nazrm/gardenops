"""Tests for the configured AI provider adapter."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch

import anthropic
import httpx
import openai

from gardenops.services.ai_provider import (
    AIProviderNotConfigured,
    AIProviderTimeout,
    chat_with_ai,
    configured_provider,
    diagnose_plant_with_ai,
    generate_task_descriptions_with_ai,
    identify_plant_with_ai,
)


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


class TestAIProviderAdapter(unittest.TestCase):
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
