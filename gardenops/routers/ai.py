"""AI-powered plant lookup, garden chat, identification, and diagnosis using Claude."""

import base64
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Literal, cast

from anthropic import Anthropic
from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import Field

from gardenops.branding import app_user_agent
from gardenops.db import DB, DbConn
from gardenops.models import StrictBaseModel
from gardenops.observability import observability_extra
from gardenops.rate_limit import (
    acquire_concurrency_slot,
    enforce_layered_rate_limit,
    env_int,
    env_nonneg_int,
    provider_limit_profile,
    reserve_daily_provider_budget,
)
from gardenops.request_body import read_body_limited
from gardenops.router_helpers import (
    effective_role,
    is_owner_or_admin,
)
from gardenops.router_helpers import (
    is_local_admin_fallback as _is_local_admin_fallback,
)
from gardenops.security import AuthContext, has_write_access, resolve_request_auth_context
from gardenops.security_metrics import record_security_event

router = APIRouter()

_log = logging.getLogger(__name__)

_context_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 300.0  # seconds


def _ai_rich_context_enabled() -> bool:
    raw = os.environ.get("AI_RICH_CONTEXT_ENABLED", "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _ai_photo_body_limit() -> int:
    return env_int("MAX_AI_PHOTO_BODY_BYTES", 5 * 1024 * 1024)


def _anthropic_client(api_key: str) -> Anthropic:
    return Anthropic(
        api_key=api_key,
        timeout=float(env_int("ANTHROPIC_API_TIMEOUT_SECONDS", 25)),
        max_retries=env_nonneg_int("ANTHROPIC_API_MAX_RETRIES", 1),
    )


_ALLOWED_LINK_DOMAINS = {
    "rhs.org.uk",
    "en.wikipedia.org",
    "snl.no",
    "plantasjen.no",
    "planteportalen.no",
    "hageglede.no",
    "vdberk.no",
    "primaferdighekk.no",
    "impecta.no",
    "rolv.no",
}
_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _log_provider_failure(
    message: str,
    *,
    upstream: str,
    feature_area: str,
    exc_info: bool = True,
    garden_id: int | None = None,
    user_id: int | None = None,
) -> None:
    _log.warning(
        message,
        exc_info=exc_info,
        extra=observability_extra(
            error_kind="upstream_failure",
            upstream=upstream,
            feature_area=feature_area,
            garden_id=garden_id,
            user_id=user_id,
        ),
    )


def _normalize_hostname(hostname: str | None) -> str:
    """Strip leading 'www.' for domain allowlist comparison."""
    if not hostname:
        return ""
    return hostname.removeprefix("www.")


def _page_mentions_plant(body: str, path: str, latin: str) -> bool:
    """Check if page body or URL path references the plant's latin name."""
    latin_lower = latin.lower()
    genus = latin_lower.split()[0] if " " in latin_lower else ""
    body_lower = body.lower()
    path_lower = path.lower().replace("-", " ")

    if latin_lower in body_lower or latin_lower in path_lower:
        return True
    # Genus-only fallback (handles taxonomic reclassifications)
    if genus and (genus in body_lower or genus in path_lower):
        return True
    return False


def _validate_plant_link(link: str, latin: str = "") -> str:
    """Validate a plant link URL and check content matches the plant.

    Returns the link if valid and content-verified, empty string otherwise.
    """
    if not link.startswith("https://"):
        return ""
    opener = urllib.request.build_opener(_NoRedirectHandler())
    current_url = link
    method = "GET" if latin else "HEAD"
    for _ in range(4):
        parsed = urllib.parse.urlparse(current_url)
        if _normalize_hostname(parsed.hostname) not in _ALLOWED_LINK_DOMAINS:
            _log.info("AI plant link rejected: domain %s not in allowlist", parsed.hostname)
            return ""
        req = urllib.request.Request(
            current_url,
            method=method,
            headers={"User-Agent": app_user_agent("link-checker")},
        )
        try:
            with opener.open(req, timeout=8) as resp:  # noqa: S310
                if latin:
                    body = resp.read(32_000).decode("utf-8", errors="ignore")
                    if not _page_mentions_plant(body, parsed.path, latin):
                        _log.info(
                            "AI plant link rejected: page %s does not mention '%s'",
                            current_url,
                            latin,
                        )
                        return ""
                return current_url
        except urllib.error.HTTPError as exc:
            if exc.code not in _REDIRECT_STATUS_CODES:
                _log.info("AI plant link rejected: unreachable %s", link)
                return ""
            location = exc.headers.get("Location", "").strip()
            if not location:
                _log.info("AI plant link rejected: redirect from %s missing Location", current_url)
                return ""
            next_url = urllib.parse.urljoin(current_url, location)
            next_parsed = urllib.parse.urlparse(next_url)
            if next_parsed.scheme != "https":
                _log.info("AI plant link rejected: redirect target %s is not https", next_url)
                return ""
            if _normalize_hostname(next_parsed.hostname) not in _ALLOWED_LINK_DOMAINS:
                _log.info(
                    "AI plant link rejected: redirect target domain %s not in allowlist",
                    next_parsed.hostname,
                )
                return ""
            current_url = next_url
            if exc.code == 303:
                method = "GET"
            continue
        except Exception:
            _log.info("AI plant link rejected: unreachable %s", link)
            return ""
    _log.info("AI plant link rejected: redirect limit exceeded for %s", link)
    return ""


TOOL_SCHEMA = {
    "name": "plant_data",
    "description": "Return structured data about a plant.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Common name of the plant (Norwegian preferred)",
            },
            "latin": {
                "type": "string",
                "description": "Latin/botanical name",
            },
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
                "description": "Plant category",
            },
            "bloom_month": {
                "type": "string",
                "description": "Bloom period, e.g. 'mai-juni' or month number",
            },
            "color": {
                "type": "string",
                "description": "Primary flower/foliage color (Norwegian)",
            },
            "hardiness": {
                "type": "string",
                "description": "RHS hardiness rating, e.g. 'H5', 'H6'",
            },
            "height_cm": {
                "type": "integer",
                "description": "Typical mature height in centimeters",
            },
            "light": {
                "type": "string",
                "description": "Light requirement (Norwegian), e.g. 'sol', 'halvskygge', 'skygge'",
            },
            "link": {
                "type": "string",
                "description": (
                    "URL to a well-known plant reference page. "
                    "ONLY use URLs you are confident exist. "
                    "Preferred sources: rhs.org.uk/plants/, "
                    "en.wikipedia.org/wiki/, snl.no/. "
                    "Return empty string if unsure."
                ),
            },
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

SYSTEM_PROMPT = (
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
                        "plt_id": {
                            "type": "string",
                            "description": "Exact plant id from the request",
                        },
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
CARE_REQUEST_PLANT_LIMIT_DEFAULT = 6


def _normalize_care_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())[:500]


def _plant_has_care(row: dict[str, Any]) -> bool:
    return any(_normalize_care_text(row.get(field, "")) for field in CARE_FIELD_NAMES)


def _care_batch_size() -> int:
    return max(1, min(env_int("AI_CARE_BATCH_SIZE", 20), 50))


def _chunk_plants(plants: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [plants[idx : idx + size] for idx in range(0, len(plants), size)]


def _care_candidates_for_context(
    db: DbConn,
    context: AuthContext,
) -> list[dict[str, Any]]:
    if context.garden_id is None:
        raise HTTPException(status_code=400, detail="Active garden is required")

    fields = (
        "p.plt_id, p.name, p.latin, p.category, p.bloom_month, p.color, "
        "p.hardiness, p.height_cm, p.light, "
        "p.care_watering, p.care_soil, p.care_planting, "
        "p.care_maintenance, p.care_notes"
    )
    if _is_local_admin_fallback(context):
        rows = db.execute(
            f"""
            SELECT DISTINCT {fields}
            FROM plants p
            LEFT JOIN plant_ownership po ON po.plt_id = p.plt_id
            WHERE po.garden_id = %s OR po.garden_id IS NULL
            ORDER BY p.name
            """,
            (int(context.garden_id),),
        ).fetchall()
    elif context.role == "admin":
        rows = db.execute(
            f"""
            SELECT {fields}
            FROM plants p
            JOIN plant_ownership po ON po.plt_id = p.plt_id
            WHERE po.garden_id = %s
            ORDER BY p.name
            """,
            (int(context.garden_id),),
        ).fetchall()
    else:
        rows = db.execute(
            f"""
            SELECT {fields}
            FROM plants p
            JOIN plant_ownership po ON po.plt_id = p.plt_id
            WHERE po.garden_id = %s AND po.owner_user_id = %s
            ORDER BY p.name
            """,
            (int(context.garden_id), int(context.user_id or 0)),
        ).fetchall()
    return [dict(row) for row in rows]


def _generate_care_batch(
    client: Anthropic,
    plants: list[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=CARE_SYSTEM_PROMPT,
        tools=cast(Any, [CARE_TOOL_SCHEMA]),
        tool_choice=cast(Any, {"type": "tool", "name": "care_instructions_batch"}),
        messages=cast(
            Any,
            [
                {
                    "role": "user",
                    "content": (
                        "Generate care instructions for these plants. "
                        "Use the metadata as hints and return concise Norwegian guidance.\n"
                        f"{json.dumps(plants, ensure_ascii=False)}"
                    ),
                },
            ],
        ),
    )
    expected_ids = {str(plant["plt_id"]) for plant in plants}
    for block_data in cast(list[Any], response.content):
        if block_data.type != "tool_use" or block_data.name != "care_instructions_batch":
            continue
        raw_plants = block_data.input.get("plants")
        if not isinstance(raw_plants, list):
            break
        generated: dict[str, dict[str, str]] = {}
        for item in raw_plants:
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
        if generated:
            return generated
        break
    raise HTTPException(500, "AI did not return care instructions")


class LookupRequest(StrictBaseModel):
    query: str = Field(min_length=1, max_length=200)


class GenerateMissingCareRequest(StrictBaseModel):
    max_plants: int | None = Field(default=None, ge=1, le=50)
    regenerate: bool = False


@router.post("/ai/plant-lookup")
def ai_plant_lookup(body: LookupRequest, request: Request, db: DB) -> dict:
    """Look up structured plant data using Claude AI."""
    record_security_event("ai_requests_total")
    record_security_event("ai_requests_plant_lookup")
    enforce_layered_rate_limit(
        request,
        bucket="ai-plant-lookup",
        identity_limit=env_int("AI_LOOKUP_RATE_LIMIT", 12),
        window_seconds=60,
        user_limit=env_nonneg_int("AI_LOOKUP_RATE_LIMIT_USER", 12),
        garden_limit=env_nonneg_int("AI_LOOKUP_RATE_LIMIT_GARDEN", 24),
        global_limit=env_nonneg_int("AI_LOOKUP_RATE_LIMIT_GLOBAL", 120),
    )
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(
            503,
            "ANTHROPIC_API_KEY not configured",
        )

    auth_context = resolve_request_auth_context(request)
    limits = provider_limit_profile("ai-plant-lookup")
    reserve_daily_provider_budget(
        db,
        feature="ai-plant-lookup",
        user_id=auth_context.user_id,
        garden_id=auth_context.garden_id,
        user_limit=int(limits["user_limit"]),
        garden_limit=int(limits["garden_limit"]),
    )

    try:
        with acquire_concurrency_slot(
            bucket="ai-plant-lookup",
            limit=int(limits["concurrency_limit"]),
        ):
            client = _anthropic_client(api_key)
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=cast(Any, [TOOL_SCHEMA]),
                tool_choice=cast(Any, {"type": "tool", "name": "plant_data"}),
                messages=cast(
                    Any,
                    [
                        {"role": "user", "content": f"Look up: {body.query}"},
                    ],
                ),
            )
    except Exception as exc:  # noqa: BLE001
        _log_provider_failure(
            "Claude plant lookup failed",
            upstream="anthropic",
            feature_area="ai-plant-lookup",
            garden_id=auth_context.garden_id,
            user_id=auth_context.user_id,
        )
        record_security_event("ai_provider_failures")
        record_security_event("ai_provider_failures_plant_lookup")
        raise HTTPException(502, "AI provider request failed") from exc

    for block_data in cast(list[Any], response.content):
        if block_data.type == "tool_use" and block_data.name == "plant_data":
            data = cast(dict[str, Any], block_data.input)
            # Validate the link if provided
            link = str(data.get("link", ""))
            if link:
                latin = str(data.get("latin", ""))
                data["link"] = _validate_plant_link(
                    link,
                    latin=latin,
                )
            return data

    raise HTTPException(500, "AI did not return plant data")


@router.post("/ai/generate-missing-care")
def generate_missing_care(
    request: Request,
    db: DB,
    body: GenerateMissingCareRequest = Body(default_factory=GenerateMissingCareRequest),
) -> dict:
    """Generate care instructions for accessible plants missing all care fields."""
    record_security_event("ai_requests_total")
    record_security_event("ai_requests_care_generation")
    enforce_layered_rate_limit(
        request,
        bucket="ai-care-instructions",
        identity_limit=env_int("AI_CARE_RATE_LIMIT", 40),
        window_seconds=600,
        user_limit=env_nonneg_int("AI_CARE_RATE_LIMIT_USER", 40),
        garden_limit=env_nonneg_int("AI_CARE_RATE_LIMIT_GARDEN", 80),
        global_limit=env_nonneg_int("AI_CARE_RATE_LIMIT_GLOBAL", 320),
    )
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(503, "ANTHROPIC_API_KEY not configured")

    auth_context = resolve_request_auth_context(request)
    if not has_write_access(auth_context):
        raise HTTPException(status_code=403, detail="Forbidden: write access required")

    candidates = _care_candidates_for_context(db, auth_context)
    missing = [plant for plant in candidates if not _plant_has_care(plant)]
    missing_before = len(missing)

    if body.regenerate:
        selected = candidates
    else:
        selected = missing
        if missing_before == 0:
            return {
                "status": "ok",
                "generated": 0,
                "missing_before": 0,
                "remaining_without_care": 0,
                "updated_plant_ids": [],
                "attempted": 0,
                "has_more": False,
            }

    request_limit = body.max_plants
    if request_limit is None:
        request_limit = env_int(
            "AI_CARE_REQUEST_PLANT_LIMIT",
            CARE_REQUEST_PLANT_LIMIT_DEFAULT,
        )
    request_limit = max(1, min(int(request_limit), 50))
    selected_missing = selected[:request_limit]

    limits = provider_limit_profile("ai-care-instructions")
    updated_ids: list[str] = []
    try:
        with acquire_concurrency_slot(
            bucket="ai-care-instructions",
            limit=int(limits["concurrency_limit"]),
        ):
            client = _anthropic_client(api_key)
            for batch in _chunk_plants(selected_missing, _care_batch_size()):
                reserve_daily_provider_budget(
                    db,
                    feature="ai-care-instructions",
                    user_id=auth_context.user_id,
                    garden_id=auth_context.garden_id,
                    user_limit=int(limits["user_limit"]),
                    garden_limit=int(limits["garden_limit"]),
                    request_count=len(batch),
                )
                generated = _generate_care_batch(client, batch)
                for plant in batch:
                    care_fields = generated.get(str(plant["plt_id"]))
                    if not care_fields:
                        continue
                    db.execute(
                        """
                        UPDATE plants
                        SET care_watering = %s,
                            care_soil = %s,
                            care_planting = %s,
                            care_maintenance = %s,
                            care_notes = %s
                        WHERE plt_id = %s
                        """,
                        (
                            care_fields["care_watering"],
                            care_fields["care_soil"],
                            care_fields["care_planting"],
                            care_fields["care_maintenance"],
                            care_fields["care_notes"],
                            str(plant["plt_id"]),
                        ),
                    )
                    updated_ids.append(str(plant["plt_id"]))
                db.commit()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        _log_provider_failure(
            "Claude care generation failed",
            upstream="anthropic",
            feature_area="ai-care-generation",
            garden_id=auth_context.garden_id,
            user_id=auth_context.user_id,
        )
        record_security_event("ai_provider_failures")
        record_security_event("ai_provider_failures_care_generation")
        raise HTTPException(502, "AI provider request failed") from exc

    if updated_ids:
        notify_garden_modified()
    total_selected = len(selected)
    return {
        "status": "ok",
        "generated": len(updated_ids),
        "missing_before": missing_before,
        "remaining_without_care": max(
            0,
            total_selected - len(updated_ids),
        ),
        "updated_plant_ids": updated_ids,
        "attempted": len(selected_missing),
        "has_more": total_selected > len(updated_ids),
    }


class ChatMessageModel(StrictBaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class ChatRequest(StrictBaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatMessageModel] = Field(default_factory=list, max_length=20)


def _context_cache_key(context: AuthContext) -> str:
    garden_key = context.garden_id if context.garden_id is not None else "none"
    role = effective_role(context)
    if context.user_id is None:
        return f"local:g{garden_key}"
    if role == "admin":
        return f"admin:{context.user_id}:g{garden_key}"
    return f"{role}:{context.user_id}:g{garden_key}"


def build_garden_context(db: DbConn, context: AuthContext) -> str:
    """Build a summary of the garden for the AI system prompt."""
    garden_id = context.garden_id
    role = effective_role(context)
    if garden_id is not None and (role in {"admin", "editor"} or _is_local_admin_fallback(context)):
        plots = db.execute(
            """
            SELECT p.plot_id, p.zone_code, p.zone_name, p.grid_row, p.grid_col,
                p.sub_zone
            FROM plots p
            JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE po.garden_id = %s
            ORDER BY p.zone_code, p.plot_number
            """,
            (garden_id,),
        ).fetchall()
        plant_rows = db.execute(
            """
            SELECT p.plt_id, p.name, p.latin, p.category,
                p.bloom_month, p.color, p.hardiness, p.height_cm,
                p.light, p.year_planted,
                pp.plot_id AS asgn_plot_id, pp.quantity AS asgn_quantity,
                p2.name AS asgn_plant_name
            FROM plants p
            JOIN plant_ownership po ON po.plt_id = p.plt_id
            LEFT JOIN (
                SELECT pp.plt_id, pp.plot_id, pp.quantity, ppo.garden_id
                FROM plot_plants pp
                JOIN plot_ownership ppo ON ppo.plot_id = pp.plot_id
            ) pp ON pp.plt_id = p.plt_id AND pp.garden_id = po.garden_id
            LEFT JOIN plants p2 ON pp.plt_id = p2.plt_id
            WHERE po.garden_id = %s
            ORDER BY p.name, pp.plot_id
            """,
            (garden_id,),
        ).fetchall()
        seen_plants: set[str] = set()
        plants: list[dict[str, Any]] = []
        assignments: list[dict] = []
        for row in plant_rows:
            plt_id = row["plt_id"]
            if plt_id not in seen_plants:
                seen_plants.add(plt_id)
                plants.append(row)
            asgn_plot = row["asgn_plot_id"]
            if asgn_plot is not None:
                assignments.append(
                    {
                        "plot_id": asgn_plot,
                        "plt_id": plt_id,
                        "quantity": row["asgn_quantity"],
                        "name": row["asgn_plant_name"] or "",
                    }
                )
    elif context.user_id is not None and garden_id is not None:
        plots = db.execute(
            """
            SELECT p.plot_id, p.zone_code, p.zone_name, p.grid_row, p.grid_col,
                p.sub_zone
            FROM plots p
            JOIN plot_ownership po ON po.plot_id = p.plot_id
            WHERE po.garden_id = %s AND po.owner_user_id = %s
            ORDER BY p.zone_code, p.plot_number
            """,
            (garden_id, context.user_id),
        ).fetchall()
        plant_rows = db.execute(
            """
            SELECT p.plt_id, p.name, p.latin, p.category,
                p.bloom_month, p.color, p.hardiness, p.height_cm,
                p.light, p.year_planted,
                pp.plot_id AS asgn_plot_id, pp.quantity AS asgn_quantity,
                p2.name AS asgn_plant_name
            FROM plants p
            JOIN plant_ownership po ON po.plt_id = p.plt_id
            LEFT JOIN (
                SELECT pp.plt_id, pp.plot_id, pp.quantity, ppo.garden_id, ppo.owner_user_id
                FROM plot_plants pp
                JOIN plot_ownership ppo ON ppo.plot_id = pp.plot_id
            ) pp ON pp.plt_id = p.plt_id
                AND pp.garden_id = po.garden_id
                AND pp.owner_user_id = po.owner_user_id
            LEFT JOIN plants p2 ON pp.plt_id = p2.plt_id
            WHERE po.garden_id = %s AND po.owner_user_id = %s
            ORDER BY p.name, pp.plot_id
            """,
            (garden_id, context.user_id),
        ).fetchall()
        seen_plants = set()
        plants: list[dict[str, Any]] = []
        assignments: list[dict] = []
        for row in plant_rows:
            plt_id = row["plt_id"]
            if plt_id not in seen_plants:
                seen_plants.add(plt_id)
                plants.append(row)
            asgn_plot = row["asgn_plot_id"]
            if asgn_plot is not None:
                assignments.append(
                    {
                        "plot_id": asgn_plot,
                        "plt_id": plt_id,
                        "quantity": row["asgn_quantity"],
                        "name": row["asgn_plant_name"] or "",
                    }
                )
    else:
        plots = []
        plants = []
        assignments = []

    zones: dict[str, list[str]] = {}
    for row in plots:
        code = row["zone_code"]
        if code not in zones:
            zones[code] = []
        zones[code].append(row["plot_id"])

    lines = [
        "Garden: 22.5m × 30m property, 22 cols × 30 rows grid.",
        f"Total: {len(plots)} plots, {len(plants)} plants, {len(assignments)} plantings.",
        "",
        "Zones:",
    ]
    for code, plot_ids in sorted(zones.items()):
        name = ""
        for row in plots:
            if row["zone_code"] == code:
                name = row["zone_name"]
                break
        lines.append(f"  {code} ({name}): {len(plot_ids)} plots")

    lines.append("")
    lines.append("Plants:")
    for p in plants:
        parts = [p["name"]]
        if p["latin"]:
            parts.append(f"({p['latin']})")
        details = []
        if p["category"]:
            details.append(p["category"])
        if p["bloom_month"]:
            details.append(f"bloom: {p['bloom_month']}")
        if p["color"]:
            details.append(f"color: {p['color']}")
        if p["hardiness"]:
            details.append(f"hardiness: {p['hardiness']}")
        if p["height_cm"]:
            details.append(f"{p['height_cm']}cm")
        if p["light"]:
            details.append(f"light: {p['light']}")
        if p["year_planted"]:
            details.append(f"planted: {p['year_planted']}")
        if details:
            parts.append("— " + ", ".join(details))
        lines.append(f"  {' '.join(parts)}")

    plot_plants: dict[str, list[str]] = {}
    for a in assignments:
        pid = a["plot_id"]
        if pid not in plot_plants:
            plot_plants[pid] = []
        qty = a["quantity"]
        name = a["name"]
        plot_plants[pid].append(
            f"{name} (×{qty})" if qty > 1 else name,
        )

    lines.append("")
    lines.append("Plot assignments:")
    for pid in sorted(plot_plants.keys()):
        lines.append(f"  {pid}: {', '.join(plot_plants[pid])}")

    return "\n".join(lines)


CHAT_SYSTEM_TEMPLATE = (
    "You are a plant expert with 40 years of hands-on gardening "
    "experience in Norway. You know every zone, plot, and plant in "
    "the user's garden.\n\n"
    "Rules:\n"
    "- Always reply in English.\n"
    "- Always factor in the Norwegian climate: short growing season, "
    "long winters, frost dates, light conditions per season.\n"
    "- When suggesting plants, provide at least 3 alternatives with "
    "a brief note on why each suits the spot.\n"
    "- Be concise. No filler. Get to the point with clear reasoning.\n"
    "- Reference actual plot IDs, zone names, and plant names from "
    "the garden data.\n\n"
    "GARDEN DATA:\n{context}"
)


def get_cached_context(db: DbConn, context: AuthContext) -> str:
    """Return garden context, using a TTL cache to avoid rebuilding."""
    now = time.monotonic()
    key = _context_cache_key(context)
    cached = _context_cache.get(key)
    if cached and (now - cached[1]) < _CACHE_TTL:
        return cached[0]
    ctx = build_garden_context(db, context)
    _context_cache[key] = (ctx, now)
    return ctx


def clear_context_cache() -> None:
    """Clear the cached garden context (called on reset)."""
    _context_cache.clear()


@router.post("/ai/garden-chat")
def garden_chat(body: ChatRequest, db: DB, request: Request) -> dict:
    """Chat with an AI garden expert using full garden context."""
    record_security_event("ai_requests_total")
    record_security_event("ai_requests_garden_chat")
    enforce_layered_rate_limit(
        request,
        bucket="ai-garden-chat",
        identity_limit=env_int("AI_CHAT_RATE_LIMIT", 8),
        window_seconds=60,
        user_limit=env_nonneg_int("AI_CHAT_RATE_LIMIT_USER", 8),
        garden_limit=env_nonneg_int("AI_CHAT_RATE_LIMIT_GARDEN", 16),
        global_limit=env_nonneg_int("AI_CHAT_RATE_LIMIT_GLOBAL", 60),
    )
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(
            503,
            "ANTHROPIC_API_KEY not configured",
        )

    auth_context = resolve_request_auth_context(request)
    limits = provider_limit_profile("ai-garden-chat")
    reserve_daily_provider_budget(
        db,
        feature="ai-garden-chat",
        user_id=auth_context.user_id,
        garden_id=auth_context.garden_id,
        user_limit=int(limits["user_limit"]),
        garden_limit=int(limits["garden_limit"]),
    )
    context = get_cached_context(db, auth_context)
    system = CHAT_SYSTEM_TEMPLATE.format(context=context)

    messages: list[dict[str, str]] = [{"role": m.role, "content": m.content} for m in body.history]
    messages.append({"role": "user", "content": body.message})

    try:
        with acquire_concurrency_slot(
            bucket="ai-garden-chat",
            limit=int(limits["concurrency_limit"]),
        ):
            client = _anthropic_client(api_key)
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2048,
                system=system,
                messages=cast(Any, messages),
            )
    except Exception as exc:  # noqa: BLE001
        _log_provider_failure(
            "Claude garden chat failed",
            upstream="anthropic",
            feature_area="ai-garden-chat",
            garden_id=auth_context.garden_id,
            user_id=auth_context.user_id,
        )
        record_security_event("ai_provider_failures")
        record_security_event("ai_provider_failures_garden_chat")
        raise HTTPException(502, "AI provider request failed") from exc

    reply = ""
    for block_data in cast(list[Any], response.content):
        if block_data.type == "text":
            reply += str(block_data.text)

    return {"reply": reply}


# ---------------------------------------------------------------------------
# Plant identification (PlantNet + Claude fallback)
# ---------------------------------------------------------------------------

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
                        "name": {
                            "type": "string",
                            "description": "Common name (Norwegian preferred)",
                        },
                        "latin": {
                            "type": "string",
                            "description": "Binomial Latin name without author",
                        },
                        "family": {
                            "type": "string",
                            "description": "Plant family",
                        },
                        "confidence": {
                            "type": "number",
                            "description": "0.0-1.0 confidence score",
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "Brief reasoning for this identification",
                        },
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


def _claude_identify_plant(
    image_bytes: bytes,
    organ: str,
    api_key: str,
) -> list[dict[str, Any]]:
    """Use Claude vision to identify a plant from a photo."""
    b64_image = base64.standard_b64encode(image_bytes).decode("ascii")

    client = _anthropic_client(api_key)
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        system=IDENTIFY_SYSTEM_PROMPT,
        tools=cast(Any, [IDENTIFY_TOOL_SCHEMA]),
        tool_choice=cast(Any, {"type": "tool", "name": "plant_candidates"}),
        messages=cast(
            Any,
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": b64_image,
                            },
                        },
                        {
                            "type": "text",
                            "text": f"Identify this plant. The photo shows the {organ}.",
                        },
                    ],
                },
            ],
        ),
    )

    for block_data in cast(list[Any], response.content):
        if block_data.type == "tool_use" and block_data.name == "plant_candidates":
            raw = block_data.input.get("candidates")
            if not isinstance(raw, list):
                continue
            result: list[dict[str, Any]] = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                item_data = cast(dict[str, object], item)
                conf = item_data.get("confidence", 0.0)
                try:
                    conf = max(0.0, min(1.0, float(cast(int | float | str, conf))))
                except (
                    TypeError,
                    ValueError,
                ):
                    conf = 0.0
                result.append(
                    {
                        "name": str(item_data.get("name", "")).strip()[:200],
                        "latin": str(item_data.get("latin", "")).strip()[:200],
                        "scientific_name": str(item_data.get("latin", "")).strip()[:200],
                        "family": str(item_data.get("family", "")).strip()[:100],
                        "confidence": round(conf, 3),
                        "source": "claude",
                        "gbif_id": "",
                    },
                )
            return result
    return []


@router.post("/ai/identify-plant")
async def identify_plant(
    request: Request,
    db: DB,
    organ: str = Query(default="auto"),
) -> dict[str, Any]:
    """Identify a plant from a photo using PlantNet + Claude fallback."""
    from gardenops.services.plantnet import (
        ALLOWED_ORGANS,
        PlantNetError,
        preprocess_image_for_identification,
    )
    from gardenops.services.plantnet import identify as plantnet_identify

    record_security_event("ai_requests_total")
    record_security_event("ai_requests_identify")
    enforce_layered_rate_limit(
        request,
        bucket="ai-identify",
        identity_limit=env_int("AI_IDENTIFY_RATE_LIMIT", 10),
        window_seconds=60,
        user_limit=env_nonneg_int("AI_IDENTIFY_RATE_LIMIT_USER", 10),
        garden_limit=env_nonneg_int("AI_IDENTIFY_RATE_LIMIT_GARDEN", 20),
        global_limit=env_nonneg_int("AI_IDENTIFY_RATE_LIMIT_GLOBAL", 100),
    )

    auth_context = resolve_request_auth_context(request)
    limits = provider_limit_profile("ai-identify")

    # Validate organ
    if organ not in ALLOWED_ORGANS:
        raise HTTPException(
            400,
            f"Invalid organ: {organ}. Must be one of: {', '.join(sorted(ALLOWED_ORGANS))}",
        )

    # Read and preprocess image
    max_photo_bytes = _ai_photo_body_limit()
    payload = await read_body_limited(request, max_photo_bytes)
    if not payload:
        raise HTTPException(400, "Image body is required")
    declared_ct = request.headers.get("content-type", "").strip().lower()
    image_bytes, _ = preprocess_image_for_identification(
        payload,
        declared_ct,
        max_bytes=max_photo_bytes,
    )

    # Check API keys
    plantnet_api_key = os.environ.get("PLANTNET_API_KEY", "")
    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not plantnet_api_key and not anthropic_api_key:
        raise HTTPException(503, "No identification API configured")

    reserve_daily_provider_budget(
        db,
        feature="ai-identify",
        user_id=auth_context.user_id,
        garden_id=auth_context.garden_id,
        user_limit=int(limits["user_limit"]),
        garden_limit=int(limits["garden_limit"]),
    )

    candidates: list[dict[str, Any]] = []
    plantnet_remaining: int | None = None
    confidence_threshold = float(
        os.environ.get("PLANTNET_CONFIDENCE_THRESHOLD", "0.40"),
    )

    # Try PlantNet first
    if plantnet_api_key:
        timeout = float(os.environ.get("PLANTNET_API_TIMEOUT_SECONDS", "8"))
        try:
            with acquire_concurrency_slot(
                bucket="ai-identify",
                limit=int(limits["concurrency_limit"]),
            ):
                result = plantnet_identify(
                    image_bytes,
                    organ,
                    plantnet_api_key,
                    timeout_seconds=timeout,
                )
            plantnet_remaining = result.remaining_requests
            for c in result.candidates:
                candidates.append(
                    {
                        "name": c.common_names[0] if c.common_names else c.latin,
                        "latin": c.latin,
                        "scientific_name": c.scientific_name,
                        "family": c.family,
                        "confidence": round(c.score, 3),
                        "source": "plantnet",
                        "gbif_id": c.gbif_id,
                    },
                )
        except PlantNetError as exc:
            _log.warning(
                "PlantNet failed (status=%d): %s",
                exc.status_code,
                exc.detail,
                extra=observability_extra(
                    error_kind="upstream_failure",
                    upstream="plantnet",
                    feature_area="ai-identify",
                    garden_id=auth_context.garden_id,
                    user_id=auth_context.user_id,
                ),
            )
            record_security_event("ai_provider_failures")
            record_security_event("ai_provider_failures_identify_plantnet")

    # Claude enrichment/fallback
    needs_claude = not candidates or (
        candidates and candidates[0]["confidence"] < confidence_threshold
    )
    if needs_claude and anthropic_api_key:
        try:
            claude_candidates = _claude_identify_plant(
                image_bytes,
                organ,
                anthropic_api_key,
            )
            existing_latins = {c["latin"].lower() for c in candidates}
            for cc in claude_candidates:
                if cc["latin"].lower() not in existing_latins:
                    candidates.append(cc)
        except Exception:
            _log_provider_failure(
                "Claude identify fallback failed",
                upstream="anthropic",
                feature_area="ai-identify",
                garden_id=auth_context.garden_id,
                user_id=auth_context.user_id,
            )
            record_security_event("ai_provider_failures")
            record_security_event("ai_provider_failures_identify_claude")
            if not candidates:
                raise HTTPException(502, "Identification service unavailable")

    if not candidates:
        raise HTTPException(502, "Identification service unavailable")

    candidates.sort(key=lambda c: c["confidence"], reverse=True)
    return {
        "candidates": candidates[:5],
        "attribution": "Identification powered by Pl@ntNet (https://plantnet.org)",
        "plantnet_remaining": plantnet_remaining,
    }


# ---------------------------------------------------------------------------
# Disease diagnosis (Claude with garden context)
# ---------------------------------------------------------------------------

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
                        "likely_cause": {
                            "type": "string",
                            "description": "Specific cause name",
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                        "description": {
                            "type": "string",
                            "description": "What you see and why",
                        },
                        "suggested_treatment": {"type": "string"},
                        "reasoning": {
                            "type": "string",
                            "description": "Why this diagnosis, referencing visual evidence",
                        },
                        "related_history": {
                            "type": "string",
                            "description": "Reference to prior issues if relevant, or empty string",
                        },
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

_VALID_ISSUE_TYPES = frozenset(
    {"pest", "disease", "fungal", "nutrient", "environmental", "damage", "other"},
)
_VALID_CONFIDENCE_LEVELS = frozenset({"high", "medium", "low"})


def _load_diagnosis_context(
    db: DbConn,
    context: AuthContext,
    plt_id: str,
    plot_id: str,
) -> dict[str, Any]:
    """Load plant data, prior issues, and journal entries for diagnosis context."""
    result: dict[str, Any] = {
        "plant_name": "",
        "plant_latin": "",
        "plant_category": "",
        "plot_id": "",
        "zone_code": "",
        "zone_name": "",
        "prior_issues": [],
        "journal_entries": [],
        "prior_issues_count": 0,
    }

    if not plt_id and not plot_id:
        return result

    garden_id = context.garden_id
    if not garden_id:
        return result

    if plt_id:
        if _is_local_admin_fallback(context):
            row = db.execute(
                "SELECT p.name, p.latin, p.category, NULL AS owner_user_id "
                "FROM plants p "
                "WHERE p.plt_id = %s",
                (plt_id,),
            ).fetchone()
        else:
            row = db.execute(
                "SELECT p.name, p.latin, p.category, po.owner_user_id "
                "FROM plants p "
                "JOIN plant_ownership po ON po.plt_id = p.plt_id "
                "WHERE p.plt_id = %s AND po.garden_id = %s",
                (plt_id, int(garden_id)),
            ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Plant not found")
        if effective_role(context) not in {"admin", "editor"} and not is_owner_or_admin(
            context,
            row["owner_user_id"],
        ):
            raise HTTPException(status_code=404, detail="Plant not found")
        result["plant_name"] = row["name"]
        result["plant_latin"] = row["latin"] or ""
        result["plant_category"] = row["category"] or ""

        issues = db.execute(
            "SELECT gi.issue_type, gi.title, gi.status, gi.created_at_ms, "
            "gi.suspected_cause, gi.treatment_plan "
            "FROM garden_issues gi "
            "JOIN garden_issue_plants gip ON gip.issue_id = gi.id "
            "WHERE gip.plt_id = %s AND gi.garden_id = %s "
            "ORDER BY gi.created_at_ms DESC LIMIT 10",
            (plt_id, int(garden_id)),
        ).fetchall()
        result["prior_issues"] = [dict(i) for i in issues]
        result["prior_issues_count"] = len(issues)

        notes_select = "je.notes" if _ai_rich_context_enabled() else "''::text AS notes"
        journals = db.execute(
            f"SELECT {notes_select}, je.occurred_on, je.event_type "
            "FROM garden_journal_entries je "
            "JOIN garden_journal_entry_plants jep ON jep.entry_id = je.id "
            "WHERE jep.plt_id = %s AND je.garden_id = %s "
            "ORDER BY je.occurred_on DESC LIMIT 5",
            (plt_id, int(garden_id)),
        ).fetchall()
        result["journal_entries"] = [dict(j) for j in journals]

    if plot_id:
        if _is_local_admin_fallback(context):
            plot_row = db.execute(
                "SELECT p.zone_code, p.zone_name, NULL AS owner_user_id "
                "FROM plots p "
                "WHERE p.plot_id = %s",
                (plot_id,),
            ).fetchone()
        else:
            plot_row = db.execute(
                "SELECT p.zone_code, p.zone_name, po.owner_user_id "
                "FROM plots p "
                "JOIN plot_ownership po ON po.plot_id = p.plot_id "
                "WHERE p.plot_id = %s AND po.garden_id = %s",
                (plot_id, int(garden_id)),
            ).fetchone()
        if not plot_row:
            raise HTTPException(status_code=404, detail="Plot not found")
        if effective_role(context) not in {"admin", "editor"} and not is_owner_or_admin(
            context,
            plot_row["owner_user_id"],
        ):
            raise HTTPException(status_code=404, detail="Plot not found")
        result["plot_id"] = plot_id
        result["zone_code"] = plot_row["zone_code"]
        result["zone_name"] = plot_row["zone_name"]

    return result


def _build_diagnosis_prompt(
    context: dict[str, Any],
    symptoms: str,
) -> str:
    """Build a text prompt with plant/garden context for diagnosis."""
    parts: list[str] = []
    if context["plant_name"]:
        parts.append(f"Plant: {context['plant_name']}")
        if context["plant_latin"]:
            parts.append(f"  Latin: {context['plant_latin']}")
        if context["plant_category"]:
            parts.append(f"  Category: {context['plant_category']}")
    if context["plot_id"]:
        parts.append(
            f"Location: Plot {context['plot_id']} in zone "
            f"{context['zone_code']} ({context['zone_name']})",
        )
    if context["prior_issues"]:
        parts.append("Prior issues on this plant:")
        for issue in context["prior_issues"]:
            title = issue.get("title", "")
            itype = issue.get("issue_type", "")
            status = issue.get("status", "")
            cause = issue.get("suspected_cause", "")
            treatment = issue.get("treatment_plan", "")
            line = f"  - [{itype}] {title} (status: {status})"
            if cause:
                line += f" cause: {cause}"
            if treatment:
                line += f" treatment: {treatment}"
            parts.append(line)
    if context["journal_entries"]:
        parts.append("Recent journal entries:")
        for entry in context["journal_entries"]:
            text = str(entry.get("notes", ""))[:200]
            date = entry.get("occurred_on", "")
            event = entry.get("event_type", "")
            parts.append(f"  - [{date}] ({event}) {text}")
    if symptoms:
        parts.append(f"User-described symptoms: {symptoms}")

    if parts:
        return "Context:\n" + "\n".join(parts) + "\n\nDiagnose the issue in this photo."
    return "Diagnose the issue in this photo."


def _claude_diagnose(
    image_bytes: bytes,
    prompt_text: str,
    api_key: str,
) -> list[dict[str, Any]]:
    """Use Claude vision to diagnose plant health issues."""
    b64_image = base64.standard_b64encode(image_bytes).decode("ascii")

    client = _anthropic_client(api_key)
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        system=DIAGNOSE_SYSTEM_PROMPT,
        tools=cast(Any, [DIAGNOSE_TOOL_SCHEMA]),
        tool_choice=cast(Any, {"type": "tool", "name": "plant_diagnoses"}),
        messages=cast(
            Any,
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": b64_image,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt_text,
                        },
                    ],
                },
            ],
        ),
    )

    for block_data in cast(list[Any], response.content):
        if block_data.type == "tool_use" and block_data.name == "plant_diagnoses":
            raw = block_data.input.get("diagnoses")
            if not isinstance(raw, list):
                continue
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
                        "suggested_treatment": str(
                            item_data.get("suggested_treatment", ""),
                        ).strip()[:2000],
                        "reasoning": str(item_data.get("reasoning", "")).strip()[:2000],
                        "related_history": str(
                            item_data.get("related_history", ""),
                        ).strip()[:500],
                    },
                )
            return result
    return []


@router.post("/ai/diagnose-plant")
async def diagnose_plant(
    request: Request,
    db: DB,
    plt_id: str = Query(default=""),
    plot_id: str = Query(default=""),
    symptoms: str = Query(default="", max_length=500),
) -> dict[str, Any]:
    """Diagnose plant health issues from a photo using Claude with garden context."""
    from gardenops.services.plantnet import preprocess_image_for_identification

    record_security_event("ai_requests_total")
    record_security_event("ai_requests_diagnose")
    enforce_layered_rate_limit(
        request,
        bucket="ai-diagnose",
        identity_limit=env_int("AI_DIAGNOSE_RATE_LIMIT", 6),
        window_seconds=60,
        user_limit=env_nonneg_int("AI_DIAGNOSE_RATE_LIMIT_USER", 6),
        garden_limit=env_nonneg_int("AI_DIAGNOSE_RATE_LIMIT_GARDEN", 12),
        global_limit=env_nonneg_int("AI_DIAGNOSE_RATE_LIMIT_GLOBAL", 60),
    )

    auth_context = resolve_request_auth_context(request)
    limits = provider_limit_profile("ai-diagnose")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(503, "ANTHROPIC_API_KEY not configured")

    # Read and preprocess image
    max_photo_bytes = _ai_photo_body_limit()
    payload = await read_body_limited(request, max_photo_bytes)
    if not payload:
        raise HTTPException(400, "Image body is required")
    declared_ct = request.headers.get("content-type", "").strip().lower()
    image_bytes, _ = preprocess_image_for_identification(
        payload,
        declared_ct,
        max_bytes=max_photo_bytes,
    )

    # Load plant/plot context
    plant_context = _load_diagnosis_context(db, auth_context, plt_id, plot_id)
    prompt_text = _build_diagnosis_prompt(plant_context, symptoms)

    reserve_daily_provider_budget(
        db,
        feature="ai-diagnose",
        user_id=auth_context.user_id,
        garden_id=auth_context.garden_id,
        user_limit=int(limits["user_limit"]),
        garden_limit=int(limits["garden_limit"]),
    )

    # Call Claude
    try:
        with acquire_concurrency_slot(
            bucket="ai-diagnose",
            limit=int(limits["concurrency_limit"]),
        ):
            diagnoses = _claude_diagnose(image_bytes, prompt_text, api_key)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        _log_provider_failure(
            "Claude diagnose failed",
            upstream="anthropic",
            feature_area="ai-diagnose",
            garden_id=auth_context.garden_id,
            user_id=auth_context.user_id,
        )
        record_security_event("ai_provider_failures")
        record_security_event("ai_provider_failures_diagnose")
        raise HTTPException(502, "Diagnosis service unavailable") from exc

    return {
        "diagnoses": diagnoses,
        "context_used": {
            "plant_name": plant_context.get("plant_name", ""),
            "plot_id": plant_context.get("plot_id", ""),
            "prior_issues_count": plant_context.get("prior_issues_count", 0),
        },
        "disclaimer": (
            "This is an AI-assisted assessment, not a definitive diagnosis. "
            "Consider consulting a local garden center for confirmation."
        ),
    }


from gardenops.events import notify_garden_modified, on_garden_modified  # noqa: E402

on_garden_modified(clear_context_cache)
