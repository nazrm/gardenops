"""PlantNet API client and image preprocessing for plant identification."""

from __future__ import annotations

import json
import logging
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from io import BytesIO

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

from gardenops.branding import app_user_agent
from gardenops.redaction import redact_external_log_text
from gardenops.services.image_safety import pillow_pixel_limit

_log = logging.getLogger(__name__)

ALLOWED_ORGANS = frozenset(
    {"auto", "leaf", "flower", "fruit", "bark", "habit", "other"},
)

_ALLOWED_IMAGE_MIMES = frozenset({"image/jpeg", "image/png", "image/webp"})
_IMAGE_FORMAT_BY_MIME = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}

_PREPROCESS_MAX_DIMENSION_DEFAULT = 1280
_PREPROCESS_MAX_BYTES_DEFAULT = 5 * 1024 * 1024  # 5 MB
_PREPROCESS_MAX_PIXELS = 24_000_000
_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlantNetCandidate:
    score: float
    scientific_name: str
    latin: str
    genus: str
    family: str
    common_names: list[str] = field(default_factory=list)
    gbif_id: str = ""


@dataclass(frozen=True)
class PlantNetResult:
    candidates: list[PlantNetCandidate]
    remaining_requests: int
    best_match: str


class PlantNetError(Exception):
    """Raised for PlantNet API failures."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"PlantNet error {status_code}: {detail}")


# ---------------------------------------------------------------------------
# Multipart encoder (no third-party deps)
# ---------------------------------------------------------------------------


def _build_multipart(
    image_bytes: bytes,
    organ: str,
    image_mime: str = "image/jpeg",
) -> tuple[bytes, str]:
    """Build multipart/form-data body. Returns (body_bytes, content_type)."""
    boundary = secrets.token_hex(16)
    ext = "jpg" if "jpeg" in image_mime else image_mime.split("/")[-1]
    parts: list[bytes] = []

    # Image part
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="images"; filename="photo.{ext}"\r\n'.encode(),
    )
    parts.append(f"Content-Type: {image_mime}\r\n\r\n".encode())
    parts.append(image_bytes)
    parts.append(b"\r\n")

    # Organ part
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="organs"\r\n\r\n')
    parts.append(organ.encode())
    parts.append(b"\r\n")

    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


# ---------------------------------------------------------------------------
# PlantNet identification
# ---------------------------------------------------------------------------


def identify(
    image_bytes: bytes,
    organ: str,
    api_key: str,
    timeout_seconds: float = 8.0,
    lang: str = "nb",
    max_results: int = 5,
) -> PlantNetResult:
    """Call PlantNet identification API.

    Raises PlantNetError on failure. Returns PlantNetResult on success.
    """
    if organ not in ALLOWED_ORGANS:
        raise ValueError(
            f"Invalid organ: {organ!r}. Must be one of: {', '.join(sorted(ALLOWED_ORGANS))}",
        )
    if not api_key:
        raise PlantNetError(0, "PlantNet API key not configured")
    if not image_bytes:
        raise PlantNetError(0, "Empty image data")

    params = urllib.parse.urlencode(
        {
            "api-key": api_key,
            "lang": lang,
            "nb-results": str(max_results),
        },
    )
    url = f"https://my-api.plantnet.org/v2/identify/all?{params}"

    body, content_type = _build_multipart(image_bytes, organ)

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": content_type,
            "User-Agent": app_user_agent("plantnet-client"),
        },
    )

    # PlantNet requires the key in the query string, so never follow redirects
    # that could forward that key-bearing URL to another host.
    opener = urllib.request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(req, timeout=timeout_seconds) as resp:  # noqa: S310
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code in _REDIRECT_STATUS_CODES:
            raise PlantNetError(exc.code, "PlantNet API redirected") from exc
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        raise PlantNetError(
            exc.code,
            redact_external_log_text(detail or str(exc), max_len=500),
        ) from exc
    except (TimeoutError, OSError) as exc:
        raise PlantNetError(0, "PlantNet API timeout") from exc

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise PlantNetError(0, "Invalid PlantNet response") from exc

    results_raw = data.get("results")
    if not isinstance(results_raw, list):
        raise PlantNetError(0, "PlantNet response missing results array")

    candidates: list[PlantNetCandidate] = []
    for item in results_raw:
        if not isinstance(item, dict):
            continue
        try:
            score = float(item.get("score", 0.0))
        except TypeError, ValueError:
            score = 0.0
        species = item.get("species") or {}
        genus_obj = species.get("genus") or {}
        family_obj = species.get("family") or {}
        gbif_obj = item.get("gbif") or {}

        candidates.append(
            PlantNetCandidate(
                score=score,
                scientific_name=str(species.get("scientificName", "")).strip(),
                latin=str(species.get("scientificNameWithoutAuthor", "")).strip(),
                genus=str(genus_obj.get("scientificNameWithoutAuthor", "")).strip(),
                family=str(family_obj.get("scientificNameWithoutAuthor", "")).strip(),
                common_names=[
                    str(n).strip()
                    for n in (species.get("commonNames") or [])
                    if isinstance(n, str) and n.strip()
                ],
                gbif_id=str(gbif_obj.get("id", "")).strip(),
            ),
        )

    try:
        remaining = int(data.get("remainingIdentificationRequests", -1))
    except TypeError, ValueError:
        remaining = -1
    best_match = str(data.get("bestMatch", "")).strip()

    _log.info(
        "PlantNet identify: %d candidates, top=%.2f, remaining=%d",
        len(candidates),
        candidates[0].score if candidates else 0.0,
        remaining,
    )

    return PlantNetResult(
        candidates=candidates,
        remaining_requests=remaining,
        best_match=best_match,
    )


# ---------------------------------------------------------------------------
# Image preprocessing
# ---------------------------------------------------------------------------


def preprocess_image_for_identification(
    payload: bytes,
    declared_content_type: str,
    max_dimension: int = _PREPROCESS_MAX_DIMENSION_DEFAULT,
    max_bytes: int = _PREPROCESS_MAX_BYTES_DEFAULT,
) -> tuple[bytes, str]:
    """Validate, resize, and compress an image for API submission.

    Returns (jpeg_bytes, "image/jpeg").
    Raises HTTPException(400/413/415) on validation failure.
    """
    if not payload:
        raise HTTPException(status_code=400, detail="Image body is required")

    if len(payload) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Image exceeds {max_bytes // (1024 * 1024)} MB limit",
        )

    mime_type = declared_content_type.split(";", 1)[0].strip().lower()
    if mime_type not in _ALLOWED_IMAGE_MIMES:
        raise HTTPException(
            status_code=415,
            detail="Unsupported image type. Allowed: JPEG, PNG, WebP.",
        )

    with pillow_pixel_limit(_PREPROCESS_MAX_PIXELS):
        try:
            with Image.open(BytesIO(payload)) as probe:
                actual_format = (probe.format or "").upper()
                width, height = probe.size
                if width <= 0 or height <= 0:
                    raise HTTPException(status_code=415, detail="Image has invalid dimensions")
                if width * height > _PREPROCESS_MAX_PIXELS:
                    raise HTTPException(status_code=413, detail="Image dimensions are too large")
                probe.verify()
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise HTTPException(
                status_code=413,
                detail="Image dimensions are too large",
            ) from exc
        except UnidentifiedImageError as exc:
            raise HTTPException(
                status_code=415,
                detail="Upload is not a valid image",
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=415,
                detail="Failed to decode uploaded image",
            ) from exc

        if actual_format != _IMAGE_FORMAT_BY_MIME[mime_type]:
            raise HTTPException(
                status_code=415,
                detail="Image content does not match declared content type",
            )

        try:
            with Image.open(BytesIO(payload)) as loaded:
                width, height = loaded.size
                if width <= 0 or height <= 0:
                    raise HTTPException(status_code=415, detail="Image has invalid dimensions")
                if width * height > _PREPROCESS_MAX_PIXELS:
                    raise HTTPException(status_code=413, detail="Image dimensions are too large")
                img = ImageOps.exif_transpose(loaded)
                img.load()

                width, height = img.size
                if width <= 0 or height <= 0:
                    raise HTTPException(
                        status_code=415,
                        detail="Image has invalid dimensions",
                    )

                if width > max_dimension or height > max_dimension:
                    img.thumbnail(
                        (max_dimension, max_dimension),
                        Image.Resampling.LANCZOS,
                    )

                if img.mode != "RGB":
                    img = img.convert("RGB")

                buf = BytesIO()
                img.save(buf, format="JPEG", quality=90, optimize=True)
                return buf.getvalue(), "image/jpeg"
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise HTTPException(
                status_code=413,
                detail="Image dimensions are too large",
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=415,
                detail="Failed to process uploaded image",
            ) from exc
