from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from gardenops.db import DbConn
from gardenops.provider_settings import get_plantnet_api_key
from gardenops.rate_limit import (
    acquire_concurrency_slot,
    provider_limit_profile,
    reserve_daily_provider_budget,
)
from gardenops.security import AuthContext
from gardenops.services.ai_provider import (
    AIProviderError,
    AIProviderNotConfigured,
    AIProviderTimeout,
    identify_plant_with_ai,
    is_ai_provider_configured,
    require_ai_provider_configured,
)
from gardenops.services.plantnet import PlantNetError
from gardenops.services.plantnet import identify as plantnet_identify


@dataclass(frozen=True)
class IdentificationResult:
    candidates: list[dict[str, Any]]
    plantnet_remaining: int | None
    warnings: list[str]


def identify_image_candidates(
    db: DbConn,
    context: AuthContext,
    *,
    image_bytes: bytes,
    organ: str,
    allow_ai_fallback: bool = True,
    require_candidate: bool = True,
    plantnet_call: Callable[..., Any] = plantnet_identify,
    ai_call: Callable[[bytes, str], list[dict[str, Any]]] = identify_plant_with_ai,
    reserve_budget: Callable[..., Any] = reserve_daily_provider_budget,
    acquire_slot: Callable[..., Any] = acquire_concurrency_slot,
) -> IdentificationResult:
    limits = provider_limit_profile("ai-identify")
    plantnet_key = get_plantnet_api_key(db) or ""
    if require_candidate and not plantnet_key and not is_ai_provider_configured():
        raise HTTPException(status_code=503, detail="No identification API configured")

    candidates: list[dict[str, Any]] = []
    warnings: list[str] = []
    plantnet_remaining: int | None = None
    if plantnet_key:
        try:
            with acquire_slot(bucket="ai-identify", limit=int(limits["concurrency_limit"])):
                reserve_budget(
                    db,
                    feature="ai-identify",
                    user_id=context.user_id,
                    garden_id=context.garden_id,
                    user_limit=int(limits["user_limit"]),
                    garden_limit=int(limits["garden_limit"]),
                )
                result = plantnet_call(
                    image_bytes,
                    organ,
                    plantnet_key,
                    timeout_seconds=float(os.environ.get("PLANTNET_API_TIMEOUT_SECONDS", "8")),
                )
            plantnet_remaining = result.remaining_requests
            candidates.extend(
                {
                    "name": candidate.common_names[0]
                    if candidate.common_names
                    else candidate.latin,
                    "latin": candidate.latin,
                    "scientific_name": candidate.scientific_name,
                    "family": candidate.family,
                    "confidence": round(candidate.score, 3),
                    "source": "plantnet",
                    "gbif_id": candidate.gbif_id,
                }
                for candidate in result.candidates
            )
        except PlantNetError:
            warnings.append("plantnet_unavailable")

    threshold = float(os.environ.get("PLANTNET_CONFIDENCE_THRESHOLD", "0.40"))
    needs_ai = allow_ai_fallback and (
        not candidates or float(candidates[0]["confidence"]) < threshold
    )
    if needs_ai:
        try:
            require_ai_provider_configured()
            with acquire_slot(bucket="ai-identify", limit=int(limits["concurrency_limit"])):
                reserve_budget(
                    db,
                    feature="ai-identify",
                    user_id=context.user_id,
                    garden_id=context.garden_id,
                    user_limit=int(limits["user_limit"]),
                    garden_limit=int(limits["garden_limit"]),
                )
                ai_candidates = ai_call(image_bytes, organ)
            existing_latins = {
                str(candidate.get("latin") or "").casefold() for candidate in candidates
            }
            candidates.extend(
                candidate
                for candidate in ai_candidates
                if str(candidate.get("latin") or "").casefold() not in existing_latins
            )
        except AIProviderNotConfigured as exc:
            if not candidates and require_candidate:
                raise HTTPException(status_code=503, detail=exc.detail) from exc
            warnings.append("ai_enrichment_not_configured")
        except AIProviderTimeout as exc:
            if not candidates and require_candidate:
                raise HTTPException(
                    status_code=504, detail="Identification service timed out"
                ) from exc
            warnings.append("ai_enrichment_timed_out")
        except AIProviderError as exc:
            if not candidates and require_candidate:
                raise HTTPException(
                    status_code=502, detail="Identification service unavailable"
                ) from exc
            warnings.append("ai_enrichment_unavailable")
        except HTTPException:
            if not candidates and require_candidate:
                raise
            warnings.append("ai_enrichment_budget_or_capacity_unavailable")

    if not candidates and require_candidate:
        raise HTTPException(status_code=502, detail="Identification service unavailable")
    candidates.sort(key=lambda candidate: float(candidate["confidence"]), reverse=True)
    return IdentificationResult(candidates[:5], plantnet_remaining, warnings)
