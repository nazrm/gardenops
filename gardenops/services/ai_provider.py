"""Configured AI provider adapter for GardenOps AI features."""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any, Literal, cast

from anthropic import Anthropic
from openai import OpenAI

from gardenops.rate_limit import env_int, env_nonneg_int

_logger = logging.getLogger(__name__)

AIProvider = Literal["anthropic", "openai"]
_SUPPORTED_PROVIDERS = frozenset({"anthropic", "openai"})

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENAI_MODEL = "gpt-5.5"
DEFAULT_OPENAI_FAST_MODEL = "gpt-5.4-mini"


class AIProviderNotConfigured(Exception):
    """Raised when the configured AI provider cannot be used."""

    def __init__(self, detail: str, *, provider: str | None = None) -> None:
        self.detail = detail
        self.provider = provider
        super().__init__(detail)


class AIProviderError(Exception):
    """Raised when the configured AI provider fails or returns unusable data."""

    def __init__(
        self,
        detail: str = "AI provider request failed",
        *,
        provider: str,
    ) -> None:
        self.detail = detail
        self.provider = provider
        super().__init__(detail)


def configured_provider() -> AIProvider:
    raw = (os.environ.get("AI_PROVIDER") or "").strip().lower()
    if not raw:
        raise AIProviderNotConfigured(
            "AI_PROVIDER not configured",
            provider=None,
        )
    if raw not in _SUPPORTED_PROVIDERS:
        raise AIProviderNotConfigured(
            "AI_PROVIDER must be one of: anthropic, openai",
            provider=raw,
        )
    return cast(AIProvider, raw)


def anthropic_model() -> str:
    return (os.environ.get("ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MODEL).strip()


def openai_model() -> str:
    return (os.environ.get("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL).strip()


def openai_fast_model() -> str:
    return (os.environ.get("OPENAI_FAST_MODEL") or DEFAULT_OPENAI_FAST_MODEL).strip()


def _provider_api_key(provider: AIProvider) -> str:
    key_name = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
    api_key = os.environ.get(key_name, "").strip()
    if not api_key:
        raise AIProviderNotConfigured(f"{key_name} not configured", provider=provider)
    return api_key


def require_ai_provider_configured() -> AIProvider:
    provider = configured_provider()
    _provider_api_key(provider)
    return provider


def is_ai_provider_configured() -> bool:
    try:
        require_ai_provider_configured()
    except AIProviderNotConfigured:
        return False
    return True


def _anthropic_client(api_key: str) -> Anthropic:
    return Anthropic(
        api_key=api_key,
        timeout=float(env_int("ANTHROPIC_API_TIMEOUT_SECONDS", 25)),
        max_retries=env_nonneg_int("ANTHROPIC_API_MAX_RETRIES", 1),
    )


def _openai_client(api_key: str) -> OpenAI:
    return OpenAI(
        api_key=api_key,
        timeout=float(env_int("OPENAI_API_TIMEOUT_SECONDS", 25)),
        max_retries=env_nonneg_int("OPENAI_API_MAX_RETRIES", 1),
    )


def _extract_anthropic_tool_input(response: Any, tool_name: str) -> dict[str, Any]:
    for block_data in cast(list[Any], response.content):
        if block_data.type == "tool_use" and block_data.name == tool_name:
            raw = block_data.input
            if isinstance(raw, dict):
                return cast(dict[str, Any], raw)
    raise AIProviderError("AI provider did not return structured data", provider="anthropic")


def _extract_anthropic_text(response: Any) -> str:
    reply = ""
    for block_data in cast(list[Any], response.content):
        if block_data.type == "text":
            reply += str(block_data.text)
    return reply


def _openai_text_format(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": {
            "type": "json_schema",
            "name": name,
            "schema": schema,
            "strict": False,
        },
    }


def _extract_openai_text(response: Any) -> str:
    output_text = getattr(response, "output_text", "")
    if isinstance(output_text, str) and output_text:
        return output_text
    output = getattr(response, "output", None)
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            content = getattr(item, "content", None)
            if content is None and isinstance(item, dict):
                content = item.get("content")
            if not isinstance(content, list):
                continue
            for block_data in content:
                text = getattr(block_data, "text", None)
                if text is None and isinstance(block_data, dict):
                    text = block_data.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _extract_openai_json(response: Any, *, provider: str = "openai") -> dict[str, Any]:
    text = _extract_openai_text(response)
    if not text:
        raise AIProviderError("AI provider did not return text output", provider=provider)
    try:
        raw = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise AIProviderError("AI provider returned invalid JSON", provider=provider) from exc
    if not isinstance(raw, dict):
        raise AIProviderError("AI provider returned invalid structured data", provider=provider)
    return cast(dict[str, Any], raw)


def _anthropic_tool_call(
    *,
    api_key: str,
    system: str,
    tool_schema: dict[str, Any],
    tool_name: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
) -> dict[str, Any]:
    client = _anthropic_client(api_key)
    response = client.messages.create(
        model=anthropic_model(),
        max_tokens=max_tokens,
        system=system,
        tools=cast(Any, [tool_schema]),
        tool_choice=cast(Any, {"type": "tool", "name": tool_name}),
        messages=cast(Any, messages),
    )
    return _extract_anthropic_tool_input(response, tool_name)


def _openai_structured_call(
    *,
    api_key: str,
    system: str,
    schema_name: str,
    schema: dict[str, Any],
    user_content: list[dict[str, Any]],
    max_tokens: int,
    model: str | None = None,
) -> dict[str, Any]:
    client = _openai_client(api_key)
    response = client.responses.create(
        model=model or openai_model(),
        instructions=system,
        input=[
            {
                "role": "user",
                "content": user_content,
            },
        ],
        text=_openai_text_format(schema_name, schema),
        max_output_tokens=max_tokens,
    )
    return _extract_openai_json(response)


def _openai_text_call(
    *,
    api_key: str,
    system: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    model: str | None = None,
) -> str:
    client = _openai_client(api_key)
    input_messages = [
        {
            "role": message["role"],
            "content": [{"type": "input_text", "text": message["content"]}],
        }
        for message in messages
    ]
    response = client.responses.create(
        model=model or openai_model(),
        instructions=system,
        input=input_messages,
        max_output_tokens=max_tokens,
    )
    return _extract_openai_text(response)


PLANT_LOOKUP_TOOL_SCHEMA = {
    "name": "plant_data",
    "description": "Return structured data about a plant.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "latin": {"type": "string"},
            "category": {
                "type": "string",
                "enum": [
                    "løk",
                    "frø",
                    "busker",
                    "baerbusker",
                    "trær",
                    "stauder",
                    "grønnsaker",
                    "urter",
                    "klatreplanter",
                    "stueplanter",
                    "sukkulenter",
                    "orkidéer",
                    "prydgress",
                ],
            },
            "bloom_month": {"type": "string"},
            "color": {"type": "string"},
            "hardiness": {"type": "string"},
            "height_cm": {"type": "integer"},
            "light": {"type": "string"},
            "link": {"type": "string"},
        },
        "required": [
            "name",
            "latin",
            "category",
            "bloom_month",
            "color",
            "hardiness",
            "height_cm",
            "light",
            "link",
        ],
    },
}

PLANT_LOOKUP_SYSTEM_PROMPT = (
    "You are a horticultural expert. Given a plant name (common or Latin), "
    "return accurate structured data using the plant_data tool. "
    "Prefer Norwegian common names and terms. "
    "For category: use 'løk' for bulbs/tubers/rhizomes, 'frø' for seed-grown "
    "annuals, 'stauder' for herbaceous perennials, 'busker' for shrubs, "
    "'baerbusker' for berry bushes, 'trær' for trees, 'urter' for herbs, "
    "'grønnsaker' for vegetables, 'klatreplanter' for climbers, "
    "'stueplanter' for houseplants, 'sukkulenter' for succulents, "
    "'orkidéer' for orchids, 'prydgress' for ornamental grasses. "
    "For hardiness use RHS ratings (H1-H7). "
    "For light use Norwegian: 'sol', 'halvskygge', 'skygge', or combinations. "
    "For link: provide a URL to a well-known reference page. "
    "Prefer rhs.org.uk/plants/ for the latin name, or en.wikipedia.org/wiki/. "
    "ONLY provide a URL you are confident is real and correct. "
    "If unsure, return an empty string for link. "
    "NEVER fabricate or guess URLs. "
    "If you cannot identify the plant, still call the tool with your best guess "
    "and set the name to what the user asked for."
)

CARE_FIELD_NAMES = (
    "care_watering",
    "care_soil",
    "care_planting",
    "care_maintenance",
    "care_notes",
)

CARE_TOOL_SCHEMA = {
    "name": "care_instructions_batch",
    "description": "Return concise care instructions for every requested plant.",
    "input_schema": {
        "type": "object",
        "properties": {
            "plants": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "plt_id": {"type": "string"},
                        "care_watering": {"type": "string"},
                        "care_soil": {"type": "string"},
                        "care_planting": {"type": "string"},
                        "care_maintenance": {"type": "string"},
                        "care_notes": {"type": "string"},
                    },
                    "required": [
                        "plt_id",
                        "care_watering",
                        "care_soil",
                        "care_planting",
                        "care_maintenance",
                        "care_notes",
                    ],
                },
            },
        },
        "required": ["plants"],
    },
}

CARE_SYSTEM_PROMPT = (
    "You are an experienced horticulturist gardening in Norway. "
    "Generate concise, practical plant care guidance in Norwegian. "
    "Use short plain-text sentences or fragments. No markdown. "
    "Tailor advice to Norwegian seasons, frost, and short growing seasons. "
    "Return one object for every requested plt_id exactly once using the tool."
)

IDENTIFY_TOOL_SCHEMA = {
    "name": "plant_candidates",
    "description": "Return ranked plant identification candidates from a photo.",
    "input_schema": {
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "latin": {"type": "string"},
                        "family": {"type": "string"},
                        "confidence": {"type": "number"},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["name", "latin", "family", "confidence", "reasoning"],
                },
            },
        },
        "required": ["candidates"],
    },
}

IDENTIFY_SYSTEM_PROMPT = (
    "You are a botanical identification expert. Given a photo of a plant, "
    "identify the most likely species. Return up to 3 ranked candidates. "
    "Prefer Norwegian common names. For confidence: 0.8+ = very confident, "
    "0.5-0.8 = likely, 0.3-0.5 = possible, <0.3 = guess. "
    "If the photo is not a plant or is too blurry to identify, return an "
    "empty candidates array. Consider: leaf shape, flower structure, growth "
    "habit, and any visible fruits/bark. Factor in that this garden is in "
    "Norway when ranking likelihood."
)

DIAGNOSE_TOOL_SCHEMA = {
    "name": "plant_diagnoses",
    "description": "Return ranked possible diagnoses for a plant health issue.",
    "input_schema": {
        "type": "object",
        "properties": {
            "diagnoses": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "issue_type": {
                            "type": "string",
                            "enum": [
                                "pest",
                                "disease",
                                "fungal",
                                "nutrient",
                                "environmental",
                                "damage",
                                "other",
                            ],
                        },
                        "likely_cause": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "description": {"type": "string"},
                        "suggested_treatment": {"type": "string"},
                        "reasoning": {"type": "string"},
                        "related_history": {"type": "string"},
                    },
                    "required": [
                        "issue_type",
                        "likely_cause",
                        "confidence",
                        "description",
                        "suggested_treatment",
                        "reasoning",
                        "related_history",
                    ],
                },
            },
        },
        "required": ["diagnoses"],
    },
}

DIAGNOSE_SYSTEM_PROMPT = (
    "You are a plant pathologist with 30 years of experience in Norwegian gardens. "
    "Given a photo of a plant with possible health issues, diagnose the most likely "
    "problems. Return up to 3 ranked diagnoses.\n\n"
    "Rules:\n"
    "- Be specific: name the disease/pest/condition, not just symptoms.\n"
    "- For confidence: 'high' = classic unmistakable symptoms, 'medium' = likely "
    "but could be something else, 'low' = possible but ambiguous.\n"
    "- If the plant looks healthy, return an empty diagnoses array.\n"
    "- Consider Norwegian climate: season, common local pests, hardiness zone.\n"
    "- If prior issues are provided, check for recurrence patterns.\n"
    "- Treatment should be practical: specific products or methods available in Norway.\n"
    "- Always reply in English.\n"
    "- issue_type must be one of: pest, disease, fungal, nutrient, environmental, damage, other.\n"
)

TASK_DESCRIPTION_TOOL_SCHEMA = {
    "name": "task_descriptions_batch",
    "description": "Return localized task descriptions with a clear why-it-matters explanation.",
    "input_schema": {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "task_key": {"type": "string"},
                        "description_en": {"type": "string"},
                        "description_no": {"type": "string"},
                    },
                    "required": ["task_key", "description_en", "description_no"],
                },
            },
        },
        "required": ["tasks"],
    },
}

TASK_DESCRIPTION_SYSTEM_PROMPT = (
    "You are a horticultural planning assistant. "
    "For each task, write one concise English description and one concise Norwegian Bokmål "
    "description. Each description must say what to do and why it matters. "
    "Use the provided plant care fields as the factual basis. "
    "When the preferred locale is Norwegian, make the Norwegian wording "
    "especially natural and direct. "
    "Keep each description practical, specific, and short enough for a task card. "
    "Do not use markdown or bullet points. Return one object per task_key exactly once."
)

_VALID_ISSUE_TYPES = frozenset(
    {"pest", "disease", "fungal", "nutrient", "environmental", "damage", "other"},
)
_VALID_CONFIDENCE_LEVELS = frozenset({"high", "medium", "low"})


def _image_content(image_bytes: bytes) -> list[dict[str, Any]]:
    b64_image = base64.standard_b64encode(image_bytes).decode("ascii")
    return [
        {
            "type": "input_image",
            "image_url": f"data:image/jpeg;base64,{b64_image}",
        },
    ]


def _anthropic_image_content(image_bytes: bytes) -> list[dict[str, Any]]:
    b64_image = base64.standard_b64encode(image_bytes).decode("ascii")
    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": b64_image,
            },
        },
    ]


def _clamp_confidence(value: object) -> float:
    try:
        return round(max(0.0, min(1.0, float(cast(int | float | str, value)))), 3)
    except TypeError, ValueError:
        return 0.0


def _normalize_identify_candidates(raw: object, *, source: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise AIProviderError(
            "AI provider did not return plant candidates",
            provider=source,
        )
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        item_data = cast(dict[str, object], item)
        latin = str(item_data.get("latin", "")).strip()[:200]
        result.append(
            {
                "name": str(item_data.get("name", "")).strip()[:200],
                "latin": latin,
                "scientific_name": str(item_data.get("scientific_name", latin)).strip()[:200],
                "family": str(item_data.get("family", "")).strip()[:100],
                "confidence": _clamp_confidence(item_data.get("confidence", 0.0)),
                "source": source,
                "gbif_id": "",
            },
        )
    return result


def _normalize_care_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())[:500]


def _normalize_care_batch(raw: object, expected_ids: set[str], *, provider: str) -> dict[str, Any]:
    if not isinstance(raw, list):
        raise AIProviderError("AI provider did not return care instructions", provider=provider)
    generated: dict[str, dict[str, str]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        item_data = cast(dict[str, object], item)
        plt_id = str(item_data.get("plt_id", "")).strip()
        if not plt_id or plt_id not in expected_ids or plt_id in generated:
            continue
        care_fields = {
            field: _normalize_care_text(item_data.get(field, "")) for field in CARE_FIELD_NAMES
        }
        if any(care_fields.values()):
            generated[plt_id] = care_fields
    if not generated:
        raise AIProviderError("AI provider did not return care instructions", provider=provider)
    return generated


def _normalize_diagnoses(raw: object, *, provider: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise AIProviderError("AI provider did not return diagnoses", provider=provider)
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        item_data = cast(dict[str, object], item)
        issue_type = str(item_data.get("issue_type", "other")).strip()
        if issue_type not in _VALID_ISSUE_TYPES:
            issue_type = "other"
        confidence = str(item_data.get("confidence", "low")).strip()
        if confidence not in _VALID_CONFIDENCE_LEVELS:
            confidence = "low"
        result.append(
            {
                "issue_type": issue_type,
                "likely_cause": str(item_data.get("likely_cause", "")).strip()[:500],
                "confidence": confidence,
                "description": str(item_data.get("description", "")).strip()[:2000],
                "suggested_treatment": str(item_data.get("suggested_treatment", "")).strip()[:2000],
                "reasoning": str(item_data.get("reasoning", "")).strip()[:2000],
                "related_history": str(item_data.get("related_history", "")).strip()[:500],
            },
        )
    return result


def lookup_plant_with_ai(query: str) -> dict[str, Any]:
    provider = configured_provider()
    api_key = _provider_api_key(provider)
    try:
        if provider == "anthropic":
            return _anthropic_tool_call(
                api_key=api_key,
                system=PLANT_LOOKUP_SYSTEM_PROMPT,
                tool_schema=PLANT_LOOKUP_TOOL_SCHEMA,
                tool_name="plant_data",
                messages=[{"role": "user", "content": f"Look up: {query}"}],
                max_tokens=1024,
            )
        return _openai_structured_call(
            api_key=api_key,
            system=PLANT_LOOKUP_SYSTEM_PROMPT,
            schema_name="plant_data",
            schema=PLANT_LOOKUP_TOOL_SCHEMA["input_schema"],
            user_content=[{"type": "input_text", "text": f"Look up: {query}"}],
            max_tokens=1024,
        )
    except AIProviderNotConfigured:
        raise
    except AIProviderError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AIProviderError(provider=provider) from exc


def generate_care_batch_with_ai(plants: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    provider = configured_provider()
    api_key = _provider_api_key(provider)
    expected_ids = {str(plant["plt_id"]) for plant in plants}
    prompt = (
        "Generate care instructions for these plants. "
        "Use the metadata as hints and return concise Norwegian guidance.\n"
        f"{json.dumps(plants, ensure_ascii=False)}"
    )
    try:
        if provider == "anthropic":
            data = _anthropic_tool_call(
                api_key=api_key,
                system=CARE_SYSTEM_PROMPT,
                tool_schema=CARE_TOOL_SCHEMA,
                tool_name="care_instructions_batch",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
            )
        else:
            data = _openai_structured_call(
                api_key=api_key,
                system=CARE_SYSTEM_PROMPT,
                schema_name="care_instructions_batch",
                schema=CARE_TOOL_SCHEMA["input_schema"],
                user_content=[{"type": "input_text", "text": prompt}],
                max_tokens=4096,
            )
        return cast(
            dict[str, dict[str, str]],
            _normalize_care_batch(data.get("plants"), expected_ids, provider=provider),
        )
    except AIProviderNotConfigured:
        raise
    except AIProviderError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AIProviderError(provider=provider) from exc


def chat_with_ai(system: str, messages: list[dict[str, str]]) -> str:
    provider = configured_provider()
    api_key = _provider_api_key(provider)
    try:
        if provider == "anthropic":
            client = _anthropic_client(api_key)
            response = client.messages.create(
                model=anthropic_model(),
                max_tokens=2048,
                system=system,
                messages=cast(Any, messages),
            )
            return _extract_anthropic_text(response)
        return _openai_text_call(
            api_key=api_key,
            system=system,
            messages=messages,
            max_tokens=2048,
        )
    except AIProviderNotConfigured:
        raise
    except AIProviderError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AIProviderError(provider=provider) from exc


def identify_plant_with_ai(image_bytes: bytes, organ: str) -> list[dict[str, Any]]:
    provider = configured_provider()
    api_key = _provider_api_key(provider)
    try:
        if provider == "anthropic":
            data = _anthropic_tool_call(
                api_key=api_key,
                system=IDENTIFY_SYSTEM_PROMPT,
                tool_schema=IDENTIFY_TOOL_SCHEMA,
                tool_name="plant_candidates",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            *_anthropic_image_content(image_bytes),
                            {
                                "type": "text",
                                "text": f"Identify this plant. The photo shows the {organ}.",
                            },
                        ],
                    },
                ],
                max_tokens=1024,
            )
        else:
            data = _openai_structured_call(
                api_key=api_key,
                system=IDENTIFY_SYSTEM_PROMPT,
                schema_name="plant_candidates",
                schema=IDENTIFY_TOOL_SCHEMA["input_schema"],
                user_content=[
                    *_image_content(image_bytes),
                    {
                        "type": "input_text",
                        "text": f"Identify this plant. The photo shows the {organ}.",
                    },
                ],
                max_tokens=1024,
            )
        return _normalize_identify_candidates(data.get("candidates"), source=provider)
    except AIProviderNotConfigured:
        raise
    except AIProviderError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AIProviderError(provider=provider) from exc


def diagnose_plant_with_ai(image_bytes: bytes, prompt_text: str) -> list[dict[str, Any]]:
    provider = configured_provider()
    api_key = _provider_api_key(provider)
    try:
        if provider == "anthropic":
            data = _anthropic_tool_call(
                api_key=api_key,
                system=DIAGNOSE_SYSTEM_PROMPT,
                tool_schema=DIAGNOSE_TOOL_SCHEMA,
                tool_name="plant_diagnoses",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            *_anthropic_image_content(image_bytes),
                            {"type": "text", "text": prompt_text},
                        ],
                    },
                ],
                max_tokens=2048,
            )
        else:
            data = _openai_structured_call(
                api_key=api_key,
                system=DIAGNOSE_SYSTEM_PROMPT,
                schema_name="plant_diagnoses",
                schema=DIAGNOSE_TOOL_SCHEMA["input_schema"],
                user_content=[
                    *_image_content(image_bytes),
                    {"type": "input_text", "text": prompt_text},
                ],
                max_tokens=2048,
            )
        return _normalize_diagnoses(data.get("diagnoses"), provider=provider)
    except AIProviderNotConfigured:
        raise
    except AIProviderError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AIProviderError(provider=provider) from exc


def generate_task_descriptions_with_ai(
    prompt_items: list[dict[str, Any]],
    *,
    preferred_locale: str,
) -> list[dict[str, Any]]:
    provider = configured_provider()
    api_key = _provider_api_key(provider)
    prompt = (
        "Generate localized task descriptions for these garden tasks. "
        f"The current user's preferred locale is '{preferred_locale}'. "
        "Return both English and Norwegian for every task.\n"
        f"{json.dumps(prompt_items, ensure_ascii=False)}"
    )
    try:
        if provider == "anthropic":
            data = _anthropic_tool_call(
                api_key=api_key,
                system=TASK_DESCRIPTION_SYSTEM_PROMPT,
                tool_schema=TASK_DESCRIPTION_TOOL_SCHEMA,
                tool_name="task_descriptions_batch",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
            )
        else:
            data = _openai_structured_call(
                api_key=api_key,
                system=TASK_DESCRIPTION_SYSTEM_PROMPT,
                schema_name="task_descriptions_batch",
                schema=TASK_DESCRIPTION_TOOL_SCHEMA["input_schema"],
                user_content=[{"type": "input_text", "text": prompt}],
                max_tokens=4096,
            )
        raw_tasks = data.get("tasks")
        if not isinstance(raw_tasks, list):
            raise AIProviderError(
                "AI provider did not return task descriptions",
                provider=provider,
            )
        return [cast(dict[str, Any], item) for item in raw_tasks if isinstance(item, dict)]
    except AIProviderNotConfigured:
        raise
    except AIProviderError:
        raise
    except Exception as exc:  # noqa: BLE001
        _logger.debug("AI task description provider failure", exc_info=True)
        raise AIProviderError(provider=provider) from exc
