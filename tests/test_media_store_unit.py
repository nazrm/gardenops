"""Unit tests for gardenops.services.media_store."""

import io
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from PIL import Image

import gardenops.db as db
from gardenops.services.media_store import (
    _validate_declared_content_type,
    _write_bytes_atomic,
    collect_orphaned_media_storage_keys,
    drain_media_cleanup_jobs,
    drain_media_cleanup_jobs_best_effort,
    enqueue_media_cleanup_jobs,
    media_garden_max_assets,
    media_garden_max_bytes,
    media_max_dimension,
    media_max_pixels,
    media_preview_max_dimension,
    media_upload_max_bytes,
    prepare_media_asset,
    resolve_storage_key,
    sanitize_original_filename,
)


class TestSanitizeOriginalFilename:
    @pytest.mark.parametrize(
        "input_val,expected",
        [
            ("photo.jpg", "photo.jpg"),
            ("/home/user/photos/image.png", "image.png"),
            ("", "upload"),
            ("...", "upload"),
        ],
    )
    def test_sanitize_exact_result(self, input_val: str, expected: str) -> None:
        assert sanitize_original_filename(input_val) == expected

    @pytest.mark.parametrize(
        "input_val,absent_chars",
        [
            ("../../etc/passwd", ["..", "/"]),
            ("..\\..\\Windows\\system32\\file.exe", ["\\", "/"]),
            ("file\x00name.jpg", ["\x00"]),
            ("file'name\".jpg", ["'", '"']),
            ("file..name.jpg", [".."]),
        ],
    )
    def test_sanitize_absent_chars(self, input_val: str, absent_chars: list[str]) -> None:
        result = sanitize_original_filename(input_val)
        for char in absent_chars:
            assert char not in result

    def test_truncation(self) -> None:
        long_name = "a" * 200 + ".jpg"
        result = sanitize_original_filename(long_name)
        assert len(result) <= 120


class TestResolveStorageKey(unittest.TestCase):
    def test_valid_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resolved_tmp = str(Path(tmp).resolve())
            with patch.dict("os.environ", {"MEDIA_STORAGE_DIR": tmp}):
                path = resolve_storage_key("original/ab/cd/file.jpg")
                assert str(path).startswith(resolved_tmp)

    def test_path_traversal_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"MEDIA_STORAGE_DIR": tmp}):
                with self.assertRaises(RuntimeError):
                    resolve_storage_key("../../etc/passwd")

    def test_atomic_write_removes_temp_file_when_replace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "original" / "asset.png"
            with (
                patch.dict("os.environ", {"MEDIA_STORAGE_DIR": tmp}),
                patch("gardenops.services.media_store.os.replace", side_effect=OSError("denied")),
                self.assertRaises(OSError),
            ):
                _write_bytes_atomic(target, b"payload")
            assert list((Path(tmp) / "tmp").iterdir()) == []


class TestValidateDeclaredContentType:
    @pytest.mark.parametrize(
        "declared,expected_mime,expected_fmt",
        [
            ("image/jpeg", "image/jpeg", "JPEG"),
            ("image/png", "image/png", "PNG"),
            ("image/webp", "image/webp", "WEBP"),
            ("image/jpeg; charset=utf-8", "image/jpeg", "JPEG"),
            ("IMAGE/JPEG", "image/jpeg", "JPEG"),
        ],
    )
    def test_accepted_type(self, declared: str, expected_mime: str, expected_fmt: str) -> None:
        mime, fmt = _validate_declared_content_type(declared)
        assert mime == expected_mime
        assert fmt == expected_fmt

    @pytest.mark.parametrize(
        "declared",
        [
            "image/gif",
            "application/pdf",
        ],
    )
    def test_rejected_type(self, declared: str) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _validate_declared_content_type(declared)
        assert exc_info.value.status_code == 415


class TestConfigurationGetters:
    @pytest.mark.parametrize(
        "env_overrides,getter,expected",
        [
            ({"MEDIA_MAX_UPLOAD_BYTES": "1000"}, media_upload_max_bytes, 1000),
            ({"MEDIA_MAX_UPLOAD_BYTES": "abc"}, media_upload_max_bytes, 6 * 1024 * 1024),
            ({"MEDIA_MAX_ASSETS_PER_GARDEN": ""}, media_garden_max_assets, 1200),
            ({"MEDIA_MAX_ASSETS_PER_GARDEN": "50"}, media_garden_max_assets, 50),
            ({"MEDIA_MAX_BYTES_PER_GARDEN": ""}, media_garden_max_bytes, 300 * 1024 * 1024),
            ({"MEDIA_MAX_DIMENSION": ""}, media_max_dimension, 6000),
            ({"MEDIA_MAX_DIMENSION": "100"}, media_max_dimension, 100),
            ({"MEDIA_MAX_DIMENSION": "10"}, media_max_dimension, 64),
            ({"MEDIA_PREVIEW_MAX_DIMENSION": ""}, media_preview_max_dimension, 1280),
            ({"MEDIA_MAX_PIXELS": ""}, media_max_pixels, 24_000_000),
            ({"MEDIA_MAX_PIXELS": "100"}, media_max_pixels, 4096),
        ],
    )
    def test_getter_with_env(
        self,
        env_overrides: dict,
        getter: Callable[[], int],
        expected: int,
    ) -> None:
        with patch.dict("os.environ", env_overrides):
            assert getter() == expected  # type: ignore[operator]

    def test_upload_max_bytes_default(self) -> None:
        import os as _os

        env_copy = dict(_os.environ)
        env_copy.pop("MEDIA_MAX_UPLOAD_BYTES", None)
        with patch.dict("os.environ", env_copy, clear=True):
            assert media_upload_max_bytes() == 6 * 1024 * 1024


class TestPrepareMediaAsset(unittest.TestCase):
    @staticmethod
    def _make_png(width: int = 40, height: int = 24) -> bytes:
        buf = io.BytesIO()
        img = Image.new("RGBA", (width, height), (80, 140, 90, 255))
        img.save(buf, format="PNG")
        return buf.getvalue()

    @staticmethod
    def _make_jpeg(width: int = 40, height: int = 24) -> bytes:
        buf = io.BytesIO()
        img = Image.new("RGB", (width, height), (80, 140, 90))
        img.save(buf, format="JPEG")
        return buf.getvalue()

    def test_valid_png(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"MEDIA_STORAGE_DIR": tmp}):
                asset = prepare_media_asset(
                    payload=self._make_png(),
                    declared_content_type="image/png",
                    original_filename="test.png",
                )
                assert asset.mime_type == "image/png"
                assert asset.width == 40
                assert asset.height == 24
                assert len(asset.original_bytes) > 0
                assert len(asset.preview_bytes) > 0

    def test_valid_jpeg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"MEDIA_STORAGE_DIR": tmp}):
                asset = prepare_media_asset(
                    payload=self._make_jpeg(),
                    declared_content_type="image/jpeg",
                    original_filename="test.jpg",
                )
                assert asset.mime_type == "image/jpeg"

    def test_empty_payload_rejected(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            prepare_media_asset(
                payload=b"",
                declared_content_type="image/png",
                original_filename="empty.png",
            )
        assert ctx.exception.status_code == 400

    def test_too_large_payload_rejected(self) -> None:
        with patch.dict("os.environ", {"MEDIA_MAX_UPLOAD_BYTES": "100"}):
            with self.assertRaises(HTTPException) as ctx:
                prepare_media_asset(
                    payload=b"\x00" * 200,
                    declared_content_type="image/png",
                    original_filename="big.png",
                )
            assert ctx.exception.status_code == 413

    def test_corrupt_image_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"MEDIA_STORAGE_DIR": tmp}):
                with self.assertRaises(HTTPException) as ctx:
                    prepare_media_asset(
                        payload=b"not an image at all",
                        declared_content_type="image/png",
                        original_filename="corrupt.png",
                    )
        assert ctx.exception.status_code == 415

    def test_pixel_limit_is_enforced_before_normalization(self) -> None:
        with patch.dict("os.environ", {"MEDIA_MAX_PIXELS": "4096"}):
            with self.assertRaises(HTTPException) as ctx:
                prepare_media_asset(
                    payload=self._make_png(65, 65),
                    declared_content_type="image/png",
                    original_filename="wide.png",
                )
        assert ctx.exception.status_code == 413

    def test_extreme_decompression_bomb_is_a_controlled_rejection(self) -> None:
        with patch(
            "gardenops.services.media_store.Image.open",
            side_effect=Image.DecompressionBombError("too many pixels"),
        ):
            with self.assertRaises(HTTPException) as ctx:
                prepare_media_asset(
                    payload=self._make_png(),
                    declared_content_type="image/png",
                    original_filename="bomb.png",
                )
        assert ctx.exception.status_code == 413

    def test_format_mismatch_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"MEDIA_STORAGE_DIR": tmp}):
                with self.assertRaises(HTTPException) as ctx:
                    prepare_media_asset(
                        payload=self._make_png(),
                        declared_content_type="image/jpeg",
                        original_filename="fake.jpg",
                    )
                assert ctx.exception.status_code == 415


class TestCollectOrphanedMediaStorageKeys(unittest.TestCase):
    def setUp(self) -> None:
        conn = db.get_db()
        try:
            rows = conn.execute(
                """
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public'
                  AND tablename != 'schema_migrations'
                """
            ).fetchall()
            tables = [row["tablename"] for row in rows]
            if tables:
                conn.execute("TRUNCATE {} CASCADE".format(", ".join(tables)))
            db.ensure_default_garden(conn)
            conn.commit()
        finally:
            db.return_db(conn)
        self.conn = db.get_db()
        row = self.conn.execute(
            "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
        ).fetchone()
        assert row is not None
        self.garden_id = int(row["id"])

    def tearDown(self) -> None:
        db.return_db(self.conn)

    def _insert_asset(self, asset_id: str) -> None:
        now_ms = db.current_timestamp_ms()
        self.conn.execute(
            """INSERT INTO media_assets
               (asset_id, garden_id, storage_key, preview_storage_key,
                original_filename, mime_type, bytes, width, height,
                created_at_ms)
               VALUES (%s, %s, %s, %s, 'test.png', 'image/png', 100, 40, 24, %s)""",
            (
                asset_id,
                self.garden_id,
                f"original/{asset_id}.png",
                f"preview/{asset_id}.png",
                now_ms,
            ),
        )
        self.conn.commit()

    def _link_asset(self, asset_id: str, target_type: str, target_id: str) -> None:
        self.conn.execute(
            "INSERT INTO media_links (asset_id, target_type, target_id) VALUES (%s, %s, %s)",
            (asset_id, target_type, target_id),
        )
        self.conn.commit()

    def test_deletes_unlinked_assets(self) -> None:
        self._insert_asset("orphan1")
        self._link_asset("orphan1", "plant", "PLT-1")
        orphaned = collect_orphaned_media_storage_keys(
            self.conn,
            garden_id=self.garden_id,
            target_type="plant",
            target_id="PLT-1",
        )
        assert len(orphaned) == 1
        remaining = self.conn.execute(
            "SELECT * FROM media_assets WHERE asset_id = 'orphan1'",
        ).fetchone()
        assert remaining is None

    def test_preserves_linked_assets(self) -> None:
        self._insert_asset("linked1")
        self._link_asset("linked1", "plant", "PLT-1")
        self._link_asset("linked1", "journal_entry", "J1")
        orphaned = collect_orphaned_media_storage_keys(
            self.conn,
            garden_id=self.garden_id,
            target_type="plant",
            target_id="PLT-1",
        )
        assert len(orphaned) == 0
        remaining = self.conn.execute(
            "SELECT * FROM media_assets WHERE asset_id = 'linked1'",
        ).fetchone()
        assert remaining is not None

    def test_cleanup_failure_is_retained_then_retry_succeeds(self) -> None:
        storage_pairs = [("original/retry.png", "preview/retry.png")]
        enqueue_media_cleanup_jobs(self.conn, storage_pairs)
        self.conn.commit()

        with patch(
            "gardenops.services.media_store.resolve_storage_key",
            side_effect=PermissionError(
                13,
                "permission denied",
                "/srv/private/gardenops/media/secret-owner/photo.png",
            ),
        ):
            failed = drain_media_cleanup_jobs(self.conn, storage_pairs=storage_pairs)
        assert failed.attempted == 1
        assert failed.failed == 1
        row = self.conn.execute(
            "SELECT attempts, last_error, last_attempt_at_ms FROM media_cleanup_jobs",
        ).fetchone()
        assert row is not None
        assert int(row["attempts"]) == 1
        assert row["last_attempt_at_ms"] is not None
        assert "\n" not in str(row["last_error"])
        assert len(str(row["last_error"])) <= 500
        assert "/srv/private" not in str(row["last_error"])
        assert "secret-owner" not in str(row["last_error"])
        assert "PermissionError errno=EACCES(13)" in str(row["last_error"])

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"MEDIA_STORAGE_DIR": tmp}):
                from gardenops.services.notification_service import (
                    run_notification_maintenance_once,
                )

                maintenance = run_notification_maintenance_once(self.conn)
        assert maintenance["media_cleanup_attempted"] == 1
        assert maintenance["media_cleanup_failed"] == 0
        assert self.conn.execute("SELECT 1 FROM media_cleanup_jobs").fetchone() is None

    def test_best_effort_cleanup_contains_post_commit_database_failure(self) -> None:
        connection = MagicMock()
        storage_pairs = [("original/asset.png", "preview/asset.png")]
        with patch(
            "gardenops.services.media_store.drain_media_cleanup_jobs",
            side_effect=RuntimeError("database unavailable"),
        ):
            result = drain_media_cleanup_jobs_best_effort(
                connection,
                storage_pairs=storage_pairs,
            )
        assert result.attempted == 0
        assert result.failed == 1
        connection.rollback.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
