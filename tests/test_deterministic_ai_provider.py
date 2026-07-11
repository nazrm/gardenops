"""Tests for the test-only deterministic AI provider fixture seam."""

from __future__ import annotations

from typing import Literal

import pytest

from gardenops.provider_settings import AiRuntimeConfig, validate_ai_provider
from gardenops.services import ai_provider


def _explode(*args: object, **kwargs: object) -> None:
    raise AssertionError("deterministic AI mode must not use provider settings or vendors")


def _runtime_config(
    provider: Literal["disabled", "openai", "anthropic"],
    *,
    openai_api_key: str | None = None,
) -> AiRuntimeConfig:
    return AiRuntimeConfig(
        provider=provider,
        openai_api_key=openai_api_key,
        anthropic_api_key=None,
        openai_model="openai-test-model",
        openai_fast_model="openai-fast-test-model",
        anthropic_model="anthropic-test-model",
    )


@pytest.fixture
def deterministic_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(ai_provider._DETERMINISTIC_AI_PROVIDER_ENV, "1")
    monkeypatch.setenv("AI_PROVIDER", "bogus")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    for name in (
        "get_ai_runtime_config",
        "_provider_api_key",
        "Anthropic",
        "OpenAI",
        "_anthropic_client",
        "_openai_client",
        "_anthropic_tool_call",
        "_openai_structured_call",
        "_openai_text_call",
    ):
        monkeypatch.setattr(ai_provider, name, _explode)


def test_deterministic_mode_returns_stable_local_fixtures_without_vendor_access(
    deterministic_mode: None,
) -> None:
    assert ai_provider.configured_provider() == "deterministic"
    assert ai_provider.require_ai_provider_configured() == "deterministic"
    assert ai_provider.is_ai_provider_configured() is True
    assert ai_provider.anthropic_model() == "gardenops-deterministic-e2e"
    assert ai_provider.openai_model() == "gardenops-deterministic-e2e"
    assert ai_provider.openai_fast_model() == "gardenops-deterministic-e2e"

    lookup = ai_provider.lookup_plant_with_ai("  Rosa canina  ")
    assert lookup == {
        "name": "Rosa canina",
        "latin": "Testus e2e",
        "category": "stauder",
        "bloom_month": "juni-august",
        "color": "green",
        "hardiness": "H5",
        "height_cm": 45,
        "light": "sol",
        "link": "",
    }

    care = ai_provider.generate_care_batch_with_ai(
        [{"plt_id": "plant-1"}, {"plt_id": "plant-2"}],
    )
    assert care == {
        "plant-1": {
            "care_watering": "Water when the topsoil is dry.",
            "care_soil": "Use well-drained garden soil.",
            "care_planting": "Plant at the same depth as the root ball.",
            "care_maintenance": "Remove damaged growth and check weekly.",
            "care_notes": "Deterministic E2E fixture.",
        },
        "plant-2": {
            "care_watering": "Water when the topsoil is dry.",
            "care_soil": "Use well-drained garden soil.",
            "care_planting": "Plant at the same depth as the root ball.",
            "care_maintenance": "Remove damaged growth and check weekly.",
            "care_notes": "Deterministic E2E fixture.",
        },
    }

    assert (
        ai_provider.chat_with_ai(
            "ignored",
            [{"role": "user", "content": "What should I do?"}],
        )
        == "Deterministic test reply: Check soil moisture before watering."
    )

    assert ai_provider.identify_plant_with_ai(b"fake-jpeg", "leaf") == [
        {
            "name": "Test rose",
            "latin": "Rosa canina",
            "scientific_name": "Rosa canina",
            "family": "Rosaceae",
            "confidence": 0.9,
            "source": "deterministic",
            "gbif_id": "",
        },
    ]
    assert ai_provider.diagnose_plant_with_ai(b"fake-jpeg", "ignored") == [
        {
            "issue_type": "environmental",
            "likely_cause": "Dry soil",
            "confidence": "low",
            "description": "The plant may be mildly drought stressed.",
            "suggested_treatment": "Water deeply, then monitor soil moisture.",
            "reasoning": "Deterministic E2E fixture.",
            "related_history": "",
        },
    ]
    assert ai_provider.generate_task_descriptions_with_ai(
        [{"task_key": "task-1"}],
        preferred_locale="no",
    ) == [
        {
            "task_key": "task-1",
            "description_en": (
                "Complete this garden task in the planned window. Why: it keeps the garden healthy."
            ),
            "description_no": (
                "Fullfør denne hageoppgaven i det planlagte tidsvinduet. "
                "Hvorfor: det holder hagen sunn."
            ),
        },
    ]


def test_deterministic_provider_is_not_an_admin_selectable_option() -> None:
    with pytest.raises(ValueError, match="ai_provider must be one of"):
        validate_ai_provider("deterministic")


@pytest.mark.parametrize(
    ("app_env", "flag"),
    [
        ("test", None),
        ("test", "0"),
        ("test", "true"),
        ("production", "1"),
    ],
)
def test_deterministic_mode_requires_test_environment_and_explicit_flag(
    monkeypatch: pytest.MonkeyPatch,
    app_env: str,
    flag: str | None,
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)
    if flag is None:
        monkeypatch.delenv(ai_provider._DETERMINISTIC_AI_PROVIDER_ENV, raising=False)
    else:
        monkeypatch.setenv(ai_provider._DETERMINISTIC_AI_PROVIDER_ENV, flag)
    monkeypatch.setattr(
        ai_provider,
        "get_ai_runtime_config",
        lambda: _runtime_config("disabled"),
    )

    with pytest.raises(ai_provider.AIProviderNotConfigured):
        ai_provider.configured_provider()
    with pytest.raises(ai_provider.AIProviderNotConfigured):
        ai_provider.require_ai_provider_configured()
    assert ai_provider.is_ai_provider_configured() is False


def test_deterministic_flag_is_ignored_outside_test_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(ai_provider._DETERMINISTIC_AI_PROVIDER_ENV, "1")
    monkeypatch.setattr(
        ai_provider,
        "get_ai_runtime_config",
        lambda: _runtime_config("openai", openai_api_key="configured-key"),
    )

    assert ai_provider.configured_provider() == "openai"
    assert ai_provider.require_ai_provider_configured() == "openai"
    assert ai_provider.is_ai_provider_configured() is True
