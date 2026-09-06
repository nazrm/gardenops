from __future__ import annotations

import os
from unittest.mock import patch

import gardenops.db as db
from gardenops.services.assistant import expire_and_cleanup_requests
from tests.base import BaseApiTest

TOKEN = "matrix-capture-test-token-0123456789abcdef"  # push-sanitizer: allow SECRET_ASSIGNMENT
ENV = {
    "MCP_ENABLED": "true",
    "MCP_BEARER_TOKEN": TOKEN,
    "MATRIX_ROOM_ID": "!garden:example.org",
    "MATRIX_ALLOWED_SENDER": "@owner:example.org",
    "MATRIX_GARDENOPS_USERNAME": "test_admin",
    "MATRIX_GARDEN_SLUG": "default",
}


class TestMatrixCapture(BaseApiTest):
    def _headers(self, **updates: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "image/png",
            "X-Matrix-Room-Id": ENV["MATRIX_ROOM_ID"],
            "X-Matrix-Event-Id": "$capture-1",
            "X-Matrix-Sender": ENV["MATRIX_ALLOWED_SENDER"],
            "X-Original-Filename": "flower.png",
        }
        headers.update(updates)
        return headers

    def test_upload_is_private_idempotent_and_temporary(self) -> None:
        payload = self._image_bytes(size=(80, 60))
        with patch.dict(os.environ, ENV, clear=False):
            first = self.client.post(
                "/api/integrations/matrix/captures",
                content=payload,
                headers=self._headers(),
            )
            repeated = self.client.post(
                "/api/integrations/matrix/captures",
                content=payload,
                headers=self._headers(),
            )
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(repeated.status_code, 201, repeated.text)
        asset_id = first.json()["capture_asset_id"]
        self.assertEqual(repeated.json()["capture_asset_id"], asset_id)
        conn = db.get_db()
        try:
            row = conn.execute(
                """
                SELECT target_type, target_id FROM media_links
                WHERE asset_id = %s
                """,
                (asset_id,),
            ).fetchone()
            self.assertEqual(
                dict(row),
                {"target_type": "matrix_capture", "target_id": "$capture-1"},
            )
        finally:
            db.return_db(conn)

        listed = self.client.get("/api/media")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertNotIn(asset_id, {item["asset_id"] for item in listed.json()["items"]})
        self.assertEqual(self.client.get(f"/api/media/{asset_id}").status_code, 404)
        self.assertEqual(self.client.get(f"/api/media/{asset_id}/preview").status_code, 404)

    def test_wrong_token_room_or_sender_is_rejected(self) -> None:
        payload = self._image_bytes()
        with patch.dict(os.environ, ENV, clear=False):
            self.assertEqual(
                self.client.post(
                    "/api/integrations/matrix/captures",
                    content=payload,
                    headers=self._headers(Authorization="Bearer wrong"),
                ).status_code,
                401,
            )
            self.assertEqual(
                self.client.post(
                    "/api/integrations/matrix/captures",
                    content=payload,
                    headers=self._headers(**{"X-Matrix-Sender": "@other:example.org"}),
                ).status_code,
                403,
            )

    def test_public_media_api_does_not_accept_matrix_capture_target(self) -> None:
        response = self.client.post(
            "/api/media/upload?target_type=matrix_capture&target_id=$event",
            content=self._image_bytes(),
            headers={"Content-Type": "image/png"},
        )
        self.assertEqual(response.status_code, 422)

    def test_upload_uses_the_stricter_ai_photo_limit(self) -> None:
        payload = self._image_bytes(size=(80, 60))
        limited_env = {**ENV, "MAX_AI_PHOTO_BODY_BYTES": str(len(payload) - 1)}
        with patch.dict(os.environ, limited_env, clear=False):
            response = self.client.post(
                "/api/integrations/matrix/captures",
                content=payload,
                headers=self._headers(**{"X-Matrix-Event-Id": "$too-large"}),
            )
        self.assertEqual(response.status_code, 413)

    def test_aged_capture_without_assistant_request_is_cleaned_up(self) -> None:
        with patch.dict(os.environ, ENV, clear=False):
            response = self.client.post(
                "/api/integrations/matrix/captures",
                content=self._image_bytes(size=(80, 60)),
                headers=self._headers(**{"X-Matrix-Event-Id": "$orphan"}),
            )
        self.assertEqual(response.status_code, 201, response.text)
        asset_id = response.json()["capture_asset_id"]
        conn = db.get_db()
        try:
            conn.execute(
                "UPDATE media_assets SET created_at_ms = 1 WHERE asset_id = %s",
                (asset_id,),
            )
            expire_and_cleanup_requests(conn, now_ms=10 * 24 * 60 * 60 * 1000)
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM media_assets WHERE asset_id = %s", (asset_id,)
                ).fetchone()
            )
            conn.rollback()
        finally:
            db.return_db(conn)
