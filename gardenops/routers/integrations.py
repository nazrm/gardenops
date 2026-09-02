from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request, status

from gardenops.db import DB
from gardenops.models import StrictBaseModel
from gardenops.rate_limit import enforce_rate_limit, env_int
from gardenops.request_body import read_body_limited
from gardenops.routers.media import (
    TargetType,
    _enforce_media_quota,
    _insert_prepared_asset_link,
)
from gardenops.services.integration_config import (
    assert_source_binding,
    integration_token_matches,
    mcp_enabled,
    resolve_assistant_binding,
)
from gardenops.services.media_store import (
    media_upload_max_bytes,
    persist_prepared_media,
    prepare_media_asset,
    unlink_storage_keys,
)

router = APIRouter()


class MatrixCaptureResponse(StrictBaseModel):
    capture_asset_id: str


def _integration_token(request: Request) -> str:
    if not mcp_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    authorization = request.headers.get("authorization", "").strip()
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not integration_token_matches(token):
        raise HTTPException(status_code=401, detail="Invalid integration credentials")
    return token


def _required_header(request: Request, name: str) -> str:
    value = request.headers.get(name, "").strip()
    if not value:
        raise HTTPException(status_code=400, detail=f"Missing {name} header")
    if len(value) > 255:
        raise HTTPException(status_code=400, detail=f"Invalid {name} header")
    return value


@router.post(
    "/integrations/matrix/captures",
    response_model=MatrixCaptureResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_matrix_capture(request: Request, db: DB) -> MatrixCaptureResponse:
    _integration_token(request)
    room_id = _required_header(request, "x-matrix-room-id")
    event_id = _required_header(request, "x-matrix-event-id")
    sender_id = _required_header(request, "x-matrix-sender")
    binding = resolve_assistant_binding(db)
    try:
        assert_source_binding(binding, room_id=room_id, sender_id=sender_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Matrix source is not authorized") from exc

    enforce_rate_limit(
        request,
        bucket="matrix_capture_uploads",
        limit=env_int("MATRIX_CAPTURE_RATE_LIMIT", 12),
        window_seconds=60,
    )
    db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"{room_id}|{event_id}",))
    existing = db.execute(
        """
        SELECT a.asset_id
        FROM media_links l
        JOIN media_assets a ON a.asset_id = l.asset_id
        WHERE a.garden_id = %s
          AND l.target_type = 'matrix_capture'
          AND l.target_id = %s
        LIMIT 1
        """,
        (binding.garden_id, event_id),
    ).fetchone()
    if existing:
        return MatrixCaptureResponse(capture_asset_id=str(existing["asset_id"]))

    max_bytes = min(
        media_upload_max_bytes(),
        env_int("MAX_AI_PHOTO_BODY_BYTES", 5 * 1024 * 1024),
    )
    content_length = request.headers.get("content-length", "").strip()
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(status_code=413, detail="Image exceeds upload size limit")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header") from None
    payload = await read_body_limited(request, max_bytes)
    prepared = prepare_media_asset(
        payload=payload,
        declared_content_type=request.headers.get("content-type", "").strip().lower(),
        original_filename=request.headers.get("x-original-filename", "").strip(),
    )
    _enforce_media_quota(db, garden_id=binding.garden_id, incoming_asset=prepared)
    try:
        persist_prepared_media(prepared)
        _insert_prepared_asset_link(
            db,
            garden_id=binding.garden_id,
            prepared=prepared,
            actor_user_id=binding.user_id,
            target_type=cast(TargetType, cast(Any, "matrix_capture")),
            target_id=event_id,
        )
        db.commit()
    except Exception:
        unlink_storage_keys(prepared.storage_key, prepared.preview_storage_key)
        db.rollback()
        raise
    return MatrixCaptureResponse(capture_asset_id=prepared.asset_id)
