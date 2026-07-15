from __future__ import annotations

import errno as errno_module
import logging
import os
import secrets
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

from gardenops.db import DbConn, current_timestamp_ms
from gardenops.services.image_safety import pillow_pixel_limit

_ROOT = Path(__file__).resolve().parents[2]
_ALLOWED_UPLOAD_MIME_TYPES: dict[str, tuple[str, str]] = {
    "image/jpeg": ("JPEG", ".jpg"),
    "image/png": ("PNG", ".png"),
    "image/webp": ("WEBP", ".webp"),
}
_FORMAT_TO_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedMediaAsset:
    asset_id: str
    storage_key: str
    preview_storage_key: str
    mime_type: str
    bytes: int
    width: int
    height: int
    original_filename: str
    original_bytes: bytes
    preview_bytes: bytes


@dataclass(frozen=True)
class MediaCleanupResult:
    attempted: int
    succeeded: int
    failed: int


def media_storage_root() -> Path:
    raw = os.environ.get("MEDIA_STORAGE_DIR", "").strip()
    root = Path(raw) if raw else (_ROOT / "media_uploads")
    root.mkdir(parents=True, exist_ok=True)
    for child in ("original", "preview", "tmp"):
        (root / child).mkdir(parents=True, exist_ok=True)
    return root


def media_upload_max_bytes() -> int:
    raw = os.environ.get("MEDIA_MAX_UPLOAD_BYTES", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return 6 * 1024 * 1024


def media_garden_max_assets() -> int:
    raw = os.environ.get("MEDIA_MAX_ASSETS_PER_GARDEN", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return 1200


def media_garden_max_bytes() -> int:
    raw = os.environ.get("MEDIA_MAX_BYTES_PER_GARDEN", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return 300 * 1024 * 1024


def media_max_dimension() -> int:
    raw = os.environ.get("MEDIA_MAX_DIMENSION", "").strip()
    if raw:
        try:
            return max(64, int(raw))
        except ValueError:
            pass
    return 6000


def media_preview_max_dimension() -> int:
    raw = os.environ.get("MEDIA_PREVIEW_MAX_DIMENSION", "").strip()
    if raw:
        try:
            return max(64, int(raw))
        except ValueError:
            pass
    return 1280


def media_max_pixels() -> int:
    raw = os.environ.get("MEDIA_MAX_PIXELS", "").strip()
    if raw:
        try:
            return max(4096, int(raw))
        except ValueError:
            pass
    return 24_000_000


def allowed_media_mime_types() -> tuple[str, ...]:
    return tuple(_ALLOWED_UPLOAD_MIME_TYPES.keys())


def sanitize_original_filename(raw: str) -> str:
    filename = (raw or "").strip().replace("\\", "/").split("/")[-1]
    if not filename:
        return "upload"
    safe = "".join(ch for ch in filename if 32 <= ord(ch) <= 126 and ch not in {'"', "'"})
    safe = safe.replace("..", ".")
    safe = safe[:120].strip(" .")
    return safe or "upload"


def resolve_storage_key(storage_key: str) -> Path:
    root = media_storage_root().resolve()
    candidate = (root / storage_key).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"Unsafe storage key outside media root: {storage_key}") from exc
    return candidate


def _validate_declared_content_type(declared_content_type: str) -> tuple[str, str]:
    mime_type = declared_content_type.split(";", 1)[0].strip().lower()
    if mime_type not in _ALLOWED_UPLOAD_MIME_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Unsupported image type. Allowed types: JPEG, PNG, WebP.",
        )
    return mime_type, _ALLOWED_UPLOAD_MIME_TYPES[mime_type][0]


def _normalize_for_format(image: Image.Image, format_name: str) -> Image.Image:
    if format_name == "JPEG":
        if image.mode != "RGB":
            return image.convert("RGB")
        return image
    has_alpha = "A" in image.getbands()
    if has_alpha and image.mode != "RGBA":
        return image.convert("RGBA")
    if not has_alpha and image.mode not in {"RGB", "L"}:
        return image.convert("RGB")
    return image


def _encode_image(image: Image.Image, format_name: str) -> bytes:
    buffer = BytesIO()
    if format_name == "JPEG":
        image.save(
            buffer,
            format="JPEG",
            quality=90,
            optimize=True,
            progressive=True,
        )
    elif format_name == "PNG":
        image.save(buffer, format="PNG", optimize=True)
    elif format_name == "WEBP":
        image.save(buffer, format="WEBP", quality=90, method=6)
    else:  # pragma: no cover - guarded by validation
        raise RuntimeError(f"Unsupported image format: {format_name}")
    return buffer.getvalue()


def _validate_media_dimensions(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise HTTPException(status_code=415, detail="Image has invalid dimensions")
    max_dimension = media_max_dimension()
    if width > max_dimension or height > max_dimension:
        raise HTTPException(
            status_code=413,
            detail="Image dimensions exceed the configured limit",
        )
    if width * height > media_max_pixels():
        raise HTTPException(
            status_code=413,
            detail="Image pixel count exceeds the configured limit",
        )


def prepare_media_asset(
    *,
    payload: bytes,
    declared_content_type: str,
    original_filename: str,
) -> PreparedMediaAsset:
    if not payload:
        raise HTTPException(status_code=400, detail="Upload body is empty")
    if len(payload) > media_upload_max_bytes():
        raise HTTPException(status_code=413, detail="Image exceeds upload size limit")

    mime_type, expected_format = _validate_declared_content_type(declared_content_type)
    with pillow_pixel_limit(media_max_pixels()):
        try:
            with Image.open(BytesIO(payload)) as probe:
                actual_format = (probe.format or "").upper()
                _validate_media_dimensions(*probe.size)
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

        if actual_format != expected_format:
            raise HTTPException(
                status_code=415,
                detail="Image content does not match declared content type",
            )

        try:
            with Image.open(BytesIO(payload)) as loaded:
                _validate_media_dimensions(*loaded.size)
                normalized = ImageOps.exif_transpose(loaded)
                normalized.load()
                width, height = normalized.size
                _validate_media_dimensions(width, height)
                safe_original = _normalize_for_format(normalized, expected_format)
                original_bytes = _encode_image(safe_original, expected_format)
                preview = safe_original.copy()
                preview.thumbnail(
                    (media_preview_max_dimension(), media_preview_max_dimension()),
                    Image.Resampling.LANCZOS,
                )
                preview_bytes = _encode_image(preview, expected_format)
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise HTTPException(
                status_code=413,
                detail="Image dimensions are too large",
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=415,
                detail="Failed to normalize uploaded image",
            ) from exc

    asset_id = secrets.token_hex(16)
    _, ext = _ALLOWED_UPLOAD_MIME_TYPES[mime_type]
    prefix = f"{asset_id[:2]}/{asset_id[2:4]}"
    original_storage_key = f"original/{prefix}/{asset_id}{ext}"
    preview_storage_key = f"preview/{prefix}/{asset_id}{ext}"
    return PreparedMediaAsset(
        asset_id=asset_id,
        storage_key=original_storage_key,
        preview_storage_key=preview_storage_key,
        mime_type=_FORMAT_TO_MIME[expected_format],
        bytes=len(original_bytes),
        width=width,
        height=height,
        original_filename=sanitize_original_filename(original_filename),
        original_bytes=original_bytes,
        preview_bytes=preview_bytes,
    )


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = media_storage_root() / "tmp"
    with tempfile.NamedTemporaryFile(dir=tmp_dir, delete=False) as handle:
        handle.write(payload)
        temp_path = Path(handle.name)
    try:
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def persist_prepared_media(asset: PreparedMediaAsset) -> None:
    try:
        _write_bytes_atomic(resolve_storage_key(asset.storage_key), asset.original_bytes)
        _write_bytes_atomic(resolve_storage_key(asset.preview_storage_key), asset.preview_bytes)
    except Exception:
        unlink_storage_keys(asset.storage_key, asset.preview_storage_key)
        raise


def unlink_storage_keys(*storage_keys: str) -> None:
    for storage_key in storage_keys:
        if not storage_key:
            continue
        try:
            resolve_storage_key(storage_key).unlink(missing_ok=True)
        except Exception:
            continue


def enqueue_media_cleanup_jobs(
    db: DbConn,
    storage_pairs: list[tuple[str, str]] | set[tuple[str, str]],
) -> None:
    now_ms = current_timestamp_ms()
    for storage_key, preview_storage_key in sorted(set(storage_pairs)):
        if not storage_key and not preview_storage_key:
            continue
        db.execute(
            """
            INSERT INTO media_cleanup_jobs (
                storage_key, preview_storage_key, created_at_ms
            )
            VALUES (%s, %s, %s)
            ON CONFLICT(storage_key, preview_storage_key) DO NOTHING
            """,
            (storage_key, preview_storage_key, now_ms),
        )


def _bounded_cleanup_error(errors: list[tuple[str, Exception]]) -> str:
    parts: list[str] = []
    for storage_key, exc in errors:
        safe_key = "".join(ch for ch in storage_key if 32 <= ord(ch) <= 126)[:160]
        category = type(exc).__name__
        if isinstance(exc, OSError) and exc.errno is not None:
            errno_name = errno_module.errorcode.get(exc.errno, "UNKNOWN")
            category = f"{category} errno={errno_name}({exc.errno})"
        parts.append(f"{safe_key}: {category}")
    return "; ".join(parts)[:500]


def drain_media_cleanup_jobs(
    db: DbConn,
    *,
    storage_pairs: list[tuple[str, str]] | set[tuple[str, str]] | None = None,
    limit: int = 200,
) -> MediaCleanupResult:
    params: list[object] = []
    where_sql = ""
    if storage_pairs is not None:
        pairs = sorted(set(storage_pairs))
        if not pairs:
            return MediaCleanupResult(attempted=0, succeeded=0, failed=0)
        clauses = []
        for storage_key, preview_storage_key in pairs:
            clauses.append("(storage_key = %s AND preview_storage_key = %s)")
            params.extend((storage_key, preview_storage_key))
        where_sql = f"WHERE {' OR '.join(clauses)}"
    params.append(max(1, min(limit, 1000)))
    rows = db.execute(
        f"""
        SELECT id, storage_key, preview_storage_key
        FROM media_cleanup_jobs
        {where_sql}
        ORDER BY created_at_ms, id
        LIMIT %s
        """,  # noqa: S608 - clauses are fixed SQL fragments
        params,
    ).fetchall()
    succeeded = 0
    failed = 0
    for row in rows:
        errors: list[tuple[str, Exception]] = []
        for storage_key in (str(row["storage_key"]), str(row["preview_storage_key"])):
            if not storage_key:
                continue
            try:
                resolve_storage_key(storage_key).unlink(missing_ok=True)
            except Exception as exc:
                errors.append((storage_key, exc))
        if errors:
            failed += 1
            db.execute(
                """
                UPDATE media_cleanup_jobs
                SET attempts = attempts + 1,
                    last_error = %s,
                    last_attempt_at_ms = %s
                WHERE id = %s
                """,
                (_bounded_cleanup_error(errors), current_timestamp_ms(), int(row["id"])),
            )
        else:
            succeeded += 1
            db.execute("DELETE FROM media_cleanup_jobs WHERE id = %s", (int(row["id"]),))
    if rows:
        db.commit()
    return MediaCleanupResult(attempted=len(rows), succeeded=succeeded, failed=failed)


def drain_media_cleanup_jobs_best_effort(
    db: DbConn,
    *,
    storage_pairs: list[tuple[str, str]] | set[tuple[str, str]],
) -> MediaCleanupResult:
    """Try immediate cleanup without changing an already-committed response."""
    pairs = sorted(set(storage_pairs))
    if not pairs:
        return MediaCleanupResult(attempted=0, succeeded=0, failed=0)
    try:
        return drain_media_cleanup_jobs(db, storage_pairs=pairs)
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        _log.warning(
            "Immediate media cleanup failed; durable jobs will retry",
            extra={"cleanup_job_count": len(pairs)},
            exc_info=True,
        )
        return MediaCleanupResult(attempted=0, succeeded=0, failed=len(pairs))


def collect_orphaned_media_storage_keys(
    db: DbConn,
    *,
    garden_id: int,
    target_type: str,
    target_id: str,
) -> list[tuple[str, str]]:
    db.execute(
        """
        DELETE FROM media_links
        WHERE target_type = %s AND target_id = %s AND asset_id IN (
            SELECT asset_id FROM media_assets WHERE garden_id = %s
        )
        """,
        (target_type, target_id, garden_id),
    )
    orphaned_rows = db.execute(
        """
        SELECT a.asset_id, a.storage_key, a.preview_storage_key
        FROM media_assets a
        LEFT JOIN media_links l ON l.asset_id = a.asset_id
        WHERE a.garden_id = %s AND l.asset_id IS NULL
        """,
        (garden_id,),
    ).fetchall()
    orphaned_asset_ids = [str(row["asset_id"]) for row in orphaned_rows]
    if orphaned_asset_ids:
        placeholders = ",".join(["%s"] * len(orphaned_asset_ids))
        db.execute(
            f"DELETE FROM media_assets WHERE asset_id IN ({placeholders})",  # noqa: S608
            orphaned_asset_ids,
        )
    storage_pairs = [
        (str(row["storage_key"]), str(row["preview_storage_key"])) for row in orphaned_rows
    ]
    enqueue_media_cleanup_jobs(db, storage_pairs)
    return storage_pairs
