from __future__ import annotations

from fastapi import HTTPException

from gardenops.db import DbConn
from gardenops.rate_limit import (
    acquire_concurrency_slot,
    env_int,
    provider_limit_profile,
    reserve_daily_provider_budget,
)
from gardenops.security import AuthContext
from gardenops.services.ai_provider import (
    AIProviderNotConfigured,
    analyze_garden_capture_with_ai,
    is_ai_provider_configured,
)
from gardenops.services.assistant_models import (
    CaptureAnalysis,
    CapturePlantCandidate,
)
from gardenops.services.media_store import media_upload_max_bytes, resolve_storage_key
from gardenops.services.plant_identification import identify_image_candidates
from gardenops.services.rhs_plant_resolver import normalize_botanical_name


def _reserve_identification_budget(db: DbConn, context: AuthContext) -> None:
    limits = provider_limit_profile("ai-identify")
    reserve_daily_provider_budget(
        db,
        feature="ai-identify",
        user_id=context.user_id,
        garden_id=context.garden_id,
        user_limit=int(limits["user_limit"]),
        garden_limit=int(limits["garden_limit"]),
    )


def _capture_bytes(db: DbConn, *, garden_id: int, asset_id: str) -> bytes:
    row = db.execute(
        """
        SELECT a.storage_key, a.bytes
        FROM media_assets a
        JOIN media_links l ON l.asset_id = a.asset_id
        WHERE a.asset_id = %s AND a.garden_id = %s
          AND l.target_type = 'matrix_capture'
        LIMIT 1
        """,
        (asset_id, garden_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Matrix capture not found")
    max_bytes = min(
        media_upload_max_bytes(),
        env_int("MAX_AI_PHOTO_BODY_BYTES", 5 * 1024 * 1024),
    )
    if int(row["bytes"]) > max_bytes:
        raise HTTPException(status_code=413, detail="Image exceeds analysis size limit")
    try:
        payload = resolve_storage_key(str(row["storage_key"])).read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=404, detail="Matrix capture file not found") from exc
    if len(payload) > max_bytes:
        raise HTTPException(status_code=413, detail="Image exceeds analysis size limit")
    return payload


def analyze_capture(
    db: DbConn,
    context: AuthContext,
    *,
    asset_id: str,
    caption: str,
    organ: str = "auto",
) -> CaptureAnalysis:
    if context.garden_id is None:
        raise HTTPException(status_code=500, detail="Missing garden context")
    image_bytes = _capture_bytes(db, garden_id=int(context.garden_id), asset_id=asset_id)
    limits = provider_limit_profile("ai-identify")
    identification = identify_image_candidates(
        db,
        context,
        image_bytes=image_bytes,
        organ=organ,
        allow_ai_fallback=False,
        require_candidate=False,
    )
    plant_candidates = [
        CapturePlantCandidate(
            name=str(candidate.get("name") or ""),
            latin=str(candidate.get("latin") or ""),
            confidence=max(0, min(float(candidate.get("confidence") or 0), 1)),
            source=str(candidate.get("source") or ""),
            taxonomy_refs=[str(candidate["gbif_id"])] if candidate.get("gbif_id") else [],
        )
        for candidate in identification.candidates
    ]

    provider_analysis: CaptureAnalysis | None = None
    if is_ai_provider_configured():
        with acquire_concurrency_slot(bucket="ai-identify", limit=int(limits["concurrency_limit"])):
            _reserve_identification_budget(db, context)
            provider_analysis = analyze_garden_capture_with_ai(image_bytes, caption)
    elif not plant_candidates:
        raise AIProviderNotConfigured("No identification API configured")

    if provider_analysis is None:
        return CaptureAnalysis(
            plant_candidates=plant_candidates,
            requires_clarification=True,
        )
    seen = {
        normalize_botanical_name(candidate.latin or candidate.name)
        for candidate in plant_candidates
    }
    for candidate in provider_analysis.plant_candidates:
        key = normalize_botanical_name(candidate.latin or candidate.name)
        if key and key not in seen:
            plant_candidates.append(candidate)
            seen.add(key)
    plant_candidates.sort(key=lambda candidate: candidate.confidence, reverse=True)
    return provider_analysis.model_copy(update={"plant_candidates": plant_candidates[:5]})


def capture_image_bytes(db: DbConn, context: AuthContext, *, asset_id: str) -> bytes:
    if context.garden_id is None:
        raise HTTPException(status_code=500, detail="Missing garden context")
    return _capture_bytes(db, garden_id=int(context.garden_id), asset_id=asset_id)
