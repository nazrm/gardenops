import hashlib
import io
import json
import os
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image

import gardenops.db as db
from tests.base import BaseApiTest, strong_password


class TestMedia(BaseApiTest):
    def _stepped_up_admin_headers(
        self,
        username: str = "media_cover_admin",
        password: str = "media-cover-pass",
    ) -> tuple[object, dict[str, str]]:
        self._create_test_user(username, password, role="admin")
        client = self._new_client()
        _, csrf = self._login_session(username, password, client=client)
        headers = self._session_headers(csrf)
        headers = self._reauth_and_refresh_headers(
            client,
            headers,
            password=strong_password(password),
        )
        return client, headers

    def test_media_upload_list_fetch_and_preview_for_plant(self) -> None:
        payload = self._image_bytes(fmt="PNG", size=(160, 120))
        uploaded = self.client.post(
            "/api/media/upload?target_type=plant&target_id=PLT-TEST",
            content=payload,
            headers={
                "content-type": "image/png",
                "x-upload-filename": "test-plant.png",
            },
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        body = uploaded.json()
        self.assertEqual(body["mime_type"], "image/png")
        self.assertEqual(body["original_filename"], "test-plant.png")
        self.assertEqual(body["targets"][0]["target_type"], "plant")
        self.assertEqual(body["targets"][0]["target_id"], "PLT-TEST")

        listed = self.client.get("/api/media?target_type=plant&target_id=PLT-TEST")
        self.assertEqual(listed.status_code, 200, listed.text)
        listed_body = listed.json()
        self.assertEqual(listed_body["total"], 1)
        self.assertEqual(listed_body["items"][0]["asset_id"], body["asset_id"])

        original = self.client.get(body["original_url"])
        self.assertEqual(original.status_code, 200, original.text)
        self.assertEqual(original.headers["content-type"], "image/png")
        original_image = Image.open(io.BytesIO(original.content))
        self.assertEqual(original_image.size, (160, 120))

        preview = self.client.get(body["preview_url"])
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.headers["content-type"], "image/png")
        preview_image = Image.open(io.BytesIO(preview.content))
        self.assertLessEqual(preview_image.size[0], 160)
        self.assertLessEqual(preview_image.size[1], 120)

    def test_media_upload_rejects_unsupported_image_type(self) -> None:
        r = self.client.post(
            "/api/media/upload?target_type=plot&target_id=B1",
            content=b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
            headers={
                "content-type": "image/svg+xml",
                "x-upload-filename": "bad.svg",
            },
        )
        self.assertEqual(r.status_code, 415)
        self.assertIn("Unsupported image type", r.json()["detail"])

    def test_media_upload_respects_size_limit(self) -> None:
        previous = os.environ.get("MEDIA_MAX_UPLOAD_BYTES")
        os.environ["MEDIA_MAX_UPLOAD_BYTES"] = "32"
        try:
            payload = self._image_bytes(fmt="PNG", size=(40, 40))
            r = self.client.post(
                "/api/media/upload?target_type=plant&target_id=PLT-TEST",
                content=payload,
                headers={
                    "content-type": "image/png",
                    "x-upload-filename": "too-large.png",
                },
            )
            self.assertEqual(r.status_code, 413, r.text)
        finally:
            if previous is None:
                os.environ.pop("MEDIA_MAX_UPLOAD_BYTES", None)
            else:
                os.environ["MEDIA_MAX_UPLOAD_BYTES"] = previous

    def test_media_upload_respects_asset_quota(self) -> None:
        previous = os.environ.get("MEDIA_MAX_ASSETS_PER_GARDEN")
        os.environ["MEDIA_MAX_ASSETS_PER_GARDEN"] = "1"
        try:
            payload = self._image_bytes(fmt="PNG")
            first = self.client.post(
                "/api/media/upload?target_type=plant&target_id=PLT-TEST",
                content=payload,
                headers={
                    "content-type": "image/png",
                    "x-upload-filename": "one.png",
                },
            )
            self.assertEqual(first.status_code, 201, first.text)
            second = self.client.post(
                "/api/media/upload?target_type=plot&target_id=B1",
                content=payload,
                headers={
                    "content-type": "image/png",
                    "x-upload-filename": "two.png",
                },
            )
            self.assertEqual(second.status_code, 413, second.text)
            self.assertIn("quota", second.json()["detail"].lower())
        finally:
            if previous is None:
                os.environ.pop("MEDIA_MAX_ASSETS_PER_GARDEN", None)
            else:
                os.environ["MEDIA_MAX_ASSETS_PER_GARDEN"] = previous

    def test_media_fetch_blocks_cross_garden_access(self) -> None:
        payload = self._image_bytes(fmt="PNG")
        uploaded = self.client.post(
            "/api/media/upload?target_type=plot&target_id=B1",
            content=payload,
            headers={
                "content-type": "image/png",
                "x-upload-filename": "plot.png",
            },
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        asset_id = uploaded.json()["asset_id"]

        conn = db.get_db()
        cursor = conn.execute(
            "INSERT INTO gardens (slug, name) VALUES (%s, %s) RETURNING id",
            ("media-other-garden", "Other Garden"),
        )
        other_garden_id = cursor.fetchone()["id"]
        conn.commit()
        db.return_db(conn)

        blocked = self.client.get(
            f"/api/media/{asset_id}",
            headers={"x-garden-id": str(other_garden_id)},
        )
        self.assertEqual(blocked.status_code, 404, blocked.text)

    def test_viewer_cannot_list_or_fetch_peer_plant_media_in_same_garden(self) -> None:
        uploaded = self.client.post(
            "/api/media/upload?target_type=plant&target_id=PLT-TEST",
            content=self._image_bytes(fmt="PNG"),
            headers={
                "content-type": "image/png",
                "x-upload-filename": "peer-plant.png",
            },
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        asset_id = uploaded.json()["asset_id"]

        self._create_test_user("media_viewer", "mediaviewerpass", role="viewer")
        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            client = self._new_client()
            _, csrf = self._login_session("media_viewer", "mediaviewerpass", client=client)
            headers = self._session_headers(csrf)

            targeted = client.get(
                "/api/media?target_type=plant&target_id=PLT-TEST",
                headers=headers,
            )
            self.assertEqual(targeted.status_code, 404, targeted.text)

            all_media = client.get("/api/media", headers=headers)
            self.assertEqual(all_media.status_code, 200, all_media.text)
            self.assertEqual(all_media.json()["total"], 0)

            summaries = client.post(
                "/api/media/summaries",
                headers=headers,
                json={"target_type": "plant", "target_ids": ["PLT-TEST"]},
            )
            self.assertEqual(summaries.status_code, 403, summaries.text)

            original = client.get(f"/api/media/{asset_id}", headers=headers)
            self.assertEqual(original.status_code, 404, original.text)

    def test_media_summaries_return_latest_asset_per_target(self) -> None:
        older = self.client.post(
            "/api/media/upload?target_type=plant&target_id=PLT-TEST",
            content=self._image_bytes(fmt="PNG", size=(120, 80)),
            headers={
                "content-type": "image/png",
                "x-upload-filename": "older.png",
            },
        )
        self.assertEqual(older.status_code, 201, older.text)
        older_asset_id = older.json()["asset_id"]

        conn = db.get_db()
        try:
            conn.execute(
                "UPDATE media_assets SET created_at_ms = created_at_ms - 10000 WHERE asset_id = %s",
                (older_asset_id,),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        newer = self.client.post(
            "/api/media/upload?target_type=plant&target_id=PLT-TEST",
            content=self._image_bytes(fmt="PNG", size=(180, 120)),
            headers={
                "content-type": "image/png",
                "x-upload-filename": "newer.png",
            },
        )
        self.assertEqual(newer.status_code, 201, newer.text)

        plot_asset = self.client.post(
            "/api/media/upload?target_type=plot&target_id=B1",
            content=self._image_bytes(fmt="PNG", size=(90, 90)),
            headers={
                "content-type": "image/png",
                "x-upload-filename": "plot.png",
            },
        )
        self.assertEqual(plot_asset.status_code, 201, plot_asset.text)

        summary = self.client.post(
            "/api/media/summaries",
            json={"target_type": "plant", "target_ids": ["PLT-TEST", "NOPE"]},
        )
        self.assertEqual(summary.status_code, 200, summary.text)
        body = summary.json()
        self.assertEqual(body["target_type"], "plant")
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["target_id"], "PLT-TEST")
        self.assertEqual(body["items"][0]["asset"]["asset_id"], older.json()["asset_id"])
        self.assertTrue(body["items"][0]["asset"]["is_cover"])

    def test_media_can_set_explicit_plant_cover_and_list_prefers_it(self) -> None:
        older = self.client.post(
            "/api/media/upload?target_type=plant&target_id=PLT-TEST",
            content=self._image_bytes(fmt="PNG", size=(120, 80)),
            headers={
                "content-type": "image/png",
                "x-upload-filename": "older.png",
            },
        )
        self.assertEqual(older.status_code, 201, older.text)
        older_asset_id = older.json()["asset_id"]

        conn = db.get_db()
        try:
            conn.execute(
                "UPDATE media_assets SET created_at_ms = created_at_ms - 10000 WHERE asset_id = %s",
                (older_asset_id,),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        newer = self.client.post(
            "/api/media/upload?target_type=plant&target_id=PLT-TEST",
            content=self._image_bytes(fmt="PNG", size=(180, 120)),
            headers={
                "content-type": "image/png",
                "x-upload-filename": "newer.png",
            },
        )
        self.assertEqual(newer.status_code, 201, newer.text)
        newer_asset_id = newer.json()["asset_id"]

        set_cover = self.client.post(
            "/api/media/plants/PLT-TEST/cover",
            json={"asset_id": newer_asset_id},
        )
        self.assertEqual(set_cover.status_code, 200, set_cover.text)
        self.assertTrue(set_cover.json()["asset"]["is_cover"])
        self.assertEqual(set_cover.json()["asset"]["asset_id"], newer_asset_id)

        listed = self.client.get("/api/media?target_type=plant&target_id=PLT-TEST")
        self.assertEqual(listed.status_code, 200, listed.text)
        listed_body = listed.json()
        self.assertEqual(listed_body["items"][0]["asset_id"], newer_asset_id)
        self.assertTrue(listed_body["items"][0]["is_cover"])

        summary = self.client.post(
            "/api/media/summaries",
            json={"target_type": "plant", "target_ids": ["PLT-TEST"]},
        )
        self.assertEqual(summary.status_code, 200, summary.text)
        self.assertEqual(summary.json()["items"][0]["asset"]["asset_id"], newer_asset_id)
        self.assertTrue(summary.json()["items"][0]["asset"]["is_cover"])

    def test_media_rejects_setting_cover_to_unlinked_asset(self) -> None:
        created = self.client.post(
            "/api/journal",
            json={
                "event_type": "observed",
                "occurred_on": "2026-03-13",
                "title": "Unlinked photo",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        entry_id = created.json()["id"]

        uploaded = self.client.post(
            f"/api/media/upload?target_type=journal_entry&target_id={entry_id}",
            content=self._image_bytes(fmt="PNG", size=(120, 80)),
            headers={
                "content-type": "image/png",
                "x-upload-filename": "journal-only.png",
            },
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)

        set_cover = self.client.post(
            "/api/media/plants/PLT-TEST/cover",
            json={"asset_id": uploaded.json()["asset_id"]},
        )
        self.assertEqual(set_cover.status_code, 404, set_cover.text)
        self.assertIn("not linked", set_cover.json()["detail"])

    def test_media_unlinking_cover_clears_cover_and_summary_falls_back(self) -> None:
        older = self.client.post(
            "/api/media/upload?target_type=plant&target_id=PLT-TEST",
            content=self._image_bytes(fmt="PNG", size=(120, 80)),
            headers={
                "content-type": "image/png",
                "x-upload-filename": "older.png",
            },
        )
        self.assertEqual(older.status_code, 201, older.text)
        older_asset_id = older.json()["asset_id"]

        newer = self.client.post(
            "/api/media/upload?target_type=plant&target_id=PLT-TEST",
            content=self._image_bytes(fmt="PNG", size=(180, 120)),
            headers={
                "content-type": "image/png",
                "x-upload-filename": "newer.png",
            },
        )
        self.assertEqual(newer.status_code, 201, newer.text)
        newer_asset_id = newer.json()["asset_id"]

        removed = self.client.delete(
            f"/api/media/{older_asset_id}/links?target_type=plant&target_id=PLT-TEST",
        )
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertTrue(removed.json()["deleted_asset"])

        conn = db.get_db()
        try:
            default_garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert default_garden is not None
            cover_row = conn.execute(
                "SELECT 1 FROM plant_media_covers WHERE garden_id = %s AND plt_id = 'PLT-TEST'",
                (int(default_garden["id"]),),
            ).fetchone()
            self.assertIsNone(cover_row)
        finally:
            db.return_db(conn)

        summary = self.client.post(
            "/api/media/summaries",
            json={"target_type": "plant", "target_ids": ["PLT-TEST"]},
        )
        self.assertEqual(summary.status_code, 200, summary.text)
        self.assertEqual(summary.json()["items"][0]["asset"]["asset_id"], newer_asset_id)
        self.assertFalse(summary.json()["items"][0]["asset"]["is_cover"])

    def test_media_summaries_include_shared_asset_targets(self) -> None:
        created = self.client.post(
            "/api/journal",
            json={
                "event_type": "observed",
                "occurred_on": "2026-03-13",
                "title": "Shared summary photo",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        entry_id = created.json()["id"]

        uploaded = self.client.post(
            f"/api/media/upload?target_type=journal_entry&target_id={entry_id}",
            content=self._image_bytes(fmt="PNG", size=(120, 80)),
            headers={
                "content-type": "image/png",
                "x-upload-filename": "shared-summary.png",
            },
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        asset_id = uploaded.json()["asset_id"]

        linked = self.client.post(
            f"/api/media/{asset_id}/links",
            json={"target_type": "plant", "target_id": "PLT-TEST"},
        )
        self.assertEqual(linked.status_code, 200, linked.text)

        summary = self.client.post(
            "/api/media/summaries",
            json={"target_type": "plant", "target_ids": ["PLT-TEST"]},
        )
        self.assertEqual(summary.status_code, 200, summary.text)
        asset = summary.json()["items"][0]["asset"]
        self.assertEqual(asset["asset_id"], asset_id)
        targets = {(target["target_type"], target["target_id"]) for target in asset["targets"]}
        self.assertIn(("plant", "PLT-TEST"), targets)
        self.assertIn(("journal_entry", str(entry_id)), targets)

    def test_media_bulk_populate_missing_covers_reuses_existing_plant_asset(self) -> None:
        covered_other = self.client.post(
            "/api/media/upload?target_type=plant&target_id=PLT-002",
            content=self._image_bytes(fmt="PNG", size=(110, 90)),
            headers={
                "content-type": "image/png",
                "x-upload-filename": "other-cover.png",
            },
        )
        self.assertEqual(covered_other.status_code, 201, covered_other.text)

        created = self.client.post(
            "/api/journal",
            json={
                "event_type": "observed",
                "occurred_on": "2026-03-13",
                "title": "Existing plant photo",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        entry_id = created.json()["id"]

        uploaded = self.client.post(
            f"/api/media/upload?target_type=journal_entry&target_id={entry_id}",
            content=self._image_bytes(fmt="PNG", size=(140, 100)),
            headers={
                "content-type": "image/png",
                "x-upload-filename": "journal-linked.png",
            },
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        asset_id = uploaded.json()["asset_id"]

        linked = self.client.post(
            f"/api/media/{asset_id}/links",
            json={"target_type": "plant", "target_id": "PLT-TEST"},
        )
        self.assertEqual(linked.status_code, 200, linked.text)

        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        os.environ["AUTH_API_KEY"] = ""
        os.environ["AUTH_ADMIN_STEP_UP_TTL_SECONDS"] = "60"
        try:
            admin_client, admin_headers = self._stepped_up_admin_headers()
            with patch(
                "gardenops.routers.media.discover_cover_from_plant_link",
                side_effect=AssertionError("remote fetch should not run"),
            ):
                result = admin_client.post(
                    "/api/media/plants/populate-missing-covers",
                    headers={
                        **admin_headers,
                        "x-action-reason": "reuse-existing-cover",
                    },
                    json={"max_plants": 10},
                )
        finally:
            os.environ["AUTH_REQUIRED"] = "false"
            os.environ.pop("AUTH_ADMIN_STEP_UP_TTL_SECONDS", None)
        self.assertEqual(result.status_code, 200, result.text)
        body = result.json()
        self.assertEqual(body["total_without_cover_before"], 1)
        self.assertEqual(body["adopted_existing"], 1)
        self.assertEqual(body["imported_remote"], 0)
        self.assertEqual(body["skipped"], 0)
        self.assertEqual(body["remaining_without_cover"], 0)
        self.assertEqual(body["items"][0]["plant_id"], "PLT-TEST")
        self.assertEqual(body["items"][0]["status"], "adopted_existing")

        summary = self.client.post(
            "/api/media/summaries",
            json={"target_type": "plant", "target_ids": ["PLT-TEST"]},
        )
        self.assertEqual(summary.status_code, 200, summary.text)
        self.assertEqual(summary.json()["items"][0]["asset"]["asset_id"], asset_id)
        self.assertTrue(summary.json()["items"][0]["asset"]["is_cover"])

    def test_media_bulk_populate_missing_covers_allows_local_admin_fallback(self) -> None:
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO plants (plt_id, name, latin, category, link)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    "PLT-LOCAL-FALLBACK",
                    "Local fallback plant",
                    "Localis fallbackii",
                    "busker",
                    "",
                ),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch(
            "gardenops.routers.media.discover_cover_from_plant_link",
            side_effect=AssertionError("remote fetch should not run"),
        ):
            response = self.client.post(
                "/api/media/plants/populate-missing-covers",
                json={"max_plants": 25},
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["total_without_cover_before"], 3)
        self.assertEqual(body["processed"], 3)
        self.assertEqual(body["skipped"], 3)
        self.assertIn(
            "PLT-LOCAL-FALLBACK",
            {str(item["plant_id"]) for item in body["items"]},
        )

    def test_media_bulk_populate_missing_covers_rejects_api_key_admin(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "api_key",
                "AUTH_API_KEY": "shared-test-key",
            },
            clear=False,
        ):
            with patch(
                "gardenops.routers.media.discover_cover_from_plant_link",
                side_effect=AssertionError("remote fetch should not run"),
            ):
                response = self.client.post(
                    "/api/media/plants/populate-missing-covers",
                    headers={"x-api-key": "shared-test-key"},
                    json={"max_plants": 1, "action_reason": "api-key-denied"},
                )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["detail"],
            "Session-backed admin authentication required",
        )

    def test_media_bulk_populate_missing_covers_audits_action_reason(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        os.environ["AUTH_API_KEY"] = ""
        try:
            admin_client, admin_headers = self._stepped_up_admin_headers(
                "media_cover_audit_admin",
                "media-audit-pass",
            )
            with patch(
                "gardenops.routers.media.discover_cover_from_plant_link",
                side_effect=AssertionError("remote fetch should not run"),
            ):
                response = admin_client.post(
                    "/api/media/plants/populate-missing-covers",
                    headers=admin_headers,
                    json={
                        "max_plants": 10,
                        "action_reason": "audit missing cover import",
                    },
                )
        finally:
            os.environ["AUTH_REQUIRED"] = "false"
            os.environ["AUTH_MODE"] = "session"
            os.environ["AUTH_API_KEY"] = ""

        self.assertEqual(response.status_code, 200, response.text)
        conn = db.get_db()
        try:
            row = conn.execute(
                """
                SELECT detail
                FROM audit_events
                WHERE path = '/api/media/plants/populate-missing-covers'
                ORDER BY id DESC
                LIMIT 1
                """,
            ).fetchone()
        finally:
            db.return_db(conn)
        self.assertIsNotNone(row)
        assert row is not None
        detail = json.loads(str(row["detail"]))
        self.assertEqual(detail["event"], "media.plant_cover_import")
        self.assertEqual(detail["action_reason"], "audit missing cover import")
        self.assertEqual(detail["skipped"], 2)

    def test_media_bulk_populate_missing_covers_requires_step_up(self) -> None:
        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        os.environ["AUTH_API_KEY"] = ""
        try:
            self._create_test_user("media_cover_stale_admin", "media-stale-pass", role="admin")
            admin_client = self._new_client()
            _, csrf = self._login_session(
                "media_cover_stale_admin",
                "media-stale-pass",
                client=admin_client,
            )
            headers = self._session_headers(csrf)
            session_token = admin_client.cookies.get("gardenops_session", "")
            self.assertTrue(session_token)
            session_hash = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
            conn = db.get_db()
            try:
                conn.execute(
                    """
                    UPDATE auth_sessions
                    SET reauthenticated_at_ms = %s
                    WHERE token_hash = %s
                    """,
                    (db.current_timestamp_ms() - (2 * 24 * 60 * 60 * 1000), session_hash),
                )
                conn.commit()
            finally:
                db.return_db(conn)
            response = admin_client.post(
                "/api/media/plants/populate-missing-covers",
                headers={
                    **headers,
                    "x-action-reason": "stale-media-cover-import",
                },
                json={"max_plants": 10},
            )
        finally:
            os.environ["AUTH_REQUIRED"] = "false"

        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"], "Recent reauthentication required")

    def test_media_missing_cover_report_tracks_skip_reasons_and_clears_after_cover_set(
        self,
    ) -> None:
        for plant_id in ("PLT-TEST", "PLT-002"):
            uploaded = self.client.post(
                f"/api/media/upload?target_type=plant&target_id={plant_id}",
                content=self._image_bytes(fmt="PNG", size=(120, 80)),
                headers={
                    "content-type": "image/png",
                    "x-upload-filename": f"{plant_id.lower()}-cover.png",
                },
            )
            self.assertEqual(uploaded.status_code, 201, uploaded.text)

        created_link_missing = self.client.post(
            "/api/plants",
            json={
                "plt_id": "PLT-REPORT-LINK",
                "name": "Needs link",
                "latin": "Linkus plantae",
                "category": "frø",
                "link": "",
            },
        )
        self.assertEqual(created_link_missing.status_code, 201, created_link_missing.text)

        created_latin_missing = self.client.post(
            "/api/plants",
            json={
                "plt_id": "PLT-REPORT-LATIN",
                "name": "Needs latin",
                "latin": "",
                "category": "frø",
                "link": "https://example.com/needs-latin",
            },
        )
        self.assertEqual(created_latin_missing.status_code, 201, created_latin_missing.text)

        created_remote_skip = self.client.post(
            "/api/plants",
            json={
                "plt_id": "PLT-REPORT-REMOTE",
                "name": "Needs review",
                "latin": "Mismatchus plantae",
                "category": "frø",
                "link": "https://example.com/mismatchus-plantae",
            },
        )
        self.assertEqual(created_remote_skip.status_code, 201, created_remote_skip.text)

        os.environ["AUTH_REQUIRED"] = "true"
        os.environ["AUTH_MODE"] = "session"
        os.environ["AUTH_API_KEY"] = ""
        try:
            admin_client, admin_headers = self._stepped_up_admin_headers(
                "media_cover_report_admin",
                "media-report-pass",
            )
            with patch(
                "gardenops.routers.media.discover_cover_from_plant_link",
                side_effect=HTTPException(
                    status_code=422, detail="Latin name did not match the linked page"
                ),
            ):
                result = admin_client.post(
                    "/api/media/plants/populate-missing-covers",
                    headers={
                        **admin_headers,
                        "x-action-reason": "populate-missing-report-covers",
                    },
                    json={"max_plants": 20},
                )
        finally:
            os.environ["AUTH_REQUIRED"] = "false"
        self.assertEqual(result.status_code, 200, result.text)
        body = result.json()
        self.assertEqual(body["skipped"], 3)

        report = self.client.get("/api/media/plants/missing-covers?limit=10")
        self.assertEqual(report.status_code, 200, report.text)
        report_body = report.json()
        self.assertEqual(report_body["total"], 3)
        report_items = {item["plant_id"]: item for item in report_body["items"]}
        self.assertEqual(report_items["PLT-REPORT-LINK"]["reason_code"], "missing_link")
        self.assertEqual(report_items["PLT-REPORT-LATIN"]["reason_code"], "missing_latin")
        self.assertEqual(report_items["PLT-REPORT-REMOTE"]["reason_code"], "remote_error")
        self.assertIn(
            "Latin name did not match", report_items["PLT-REPORT-REMOTE"]["status_detail"]
        )
        self.assertIsNotNone(report_items["PLT-REPORT-LINK"]["attempted_at_ms"])
        self.assertFalse(report_items["PLT-REPORT-LINK"]["has_existing_media"])

        uploaded_cover = self.client.post(
            "/api/media/upload?target_type=plant&target_id=PLT-REPORT-REMOTE",
            content=self._image_bytes(fmt="PNG", size=(180, 120)),
            headers={
                "content-type": "image/png",
                "x-upload-filename": "manual-cover.png",
            },
        )
        self.assertEqual(uploaded_cover.status_code, 201, uploaded_cover.text)

        report_after_cover = self.client.get("/api/media/plants/missing-covers?limit=10")
        self.assertEqual(report_after_cover.status_code, 200, report_after_cover.text)
        report_after_cover_ids = {item["plant_id"] for item in report_after_cover.json()["items"]}
        self.assertNotIn("PLT-REPORT-REMOTE", report_after_cover_ids)

    def test_media_delete_asset_removes_shared_asset_from_all_targets(self) -> None:
        created = self.client.post(
            "/api/journal",
            json={
                "event_type": "observed",
                "occurred_on": "2026-03-13",
                "title": "Shared delete",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        entry_id = created.json()["id"]

        uploaded = self.client.post(
            f"/api/media/upload?target_type=journal_entry&target_id={entry_id}",
            content=self._image_bytes(fmt="PNG"),
            headers={
                "content-type": "image/png",
                "x-upload-filename": "delete-all.png",
            },
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        asset_id = uploaded.json()["asset_id"]

        linked = self.client.post(
            f"/api/media/{asset_id}/links",
            json={"target_type": "plant", "target_id": "PLT-TEST"},
        )
        self.assertEqual(linked.status_code, 200, linked.text)

        deleted = self.client.delete(f"/api/media/{asset_id}")
        self.assertEqual(deleted.status_code, 200, deleted.text)

        journal_list = self.client.get(f"/api/media?target_type=journal_entry&target_id={entry_id}")
        self.assertEqual(journal_list.status_code, 200, journal_list.text)
        self.assertEqual(journal_list.json()["total"], 0)

        plant_list = self.client.get("/api/media?target_type=plant&target_id=PLT-TEST")
        self.assertEqual(plant_list.status_code, 200, plant_list.text)
        self.assertEqual(plant_list.json()["total"], 0)

    def test_media_link_remove_unlinks_single_target_without_deleting_shared_asset(self) -> None:
        created = self.client.post(
            "/api/journal",
            json={
                "event_type": "observed",
                "occurred_on": "2026-03-13",
                "title": "Shared photo",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        entry_id = created.json()["id"]

        payload = self._image_bytes(fmt="PNG")
        uploaded = self.client.post(
            f"/api/media/upload?target_type=journal_entry&target_id={entry_id}",
            content=payload,
            headers={
                "content-type": "image/png",
                "x-upload-filename": "shared.png",
            },
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        asset_id = uploaded.json()["asset_id"]

        linked = self.client.post(
            f"/api/media/{asset_id}/links",
            json={"target_type": "plant", "target_id": "PLT-TEST"},
        )
        self.assertEqual(linked.status_code, 200, linked.text)

        removed = self.client.delete(
            f"/api/media/{asset_id}/links?target_type=journal_entry&target_id={entry_id}",
        )
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertFalse(removed.json()["deleted_asset"])

        journal_list = self.client.get(f"/api/media?target_type=journal_entry&target_id={entry_id}")
        self.assertEqual(journal_list.status_code, 200, journal_list.text)
        self.assertEqual(journal_list.json()["total"], 0)

        plant_list = self.client.get("/api/media?target_type=plant&target_id=PLT-TEST")
        self.assertEqual(plant_list.status_code, 200, plant_list.text)
        self.assertEqual(plant_list.json()["total"], 1)
        self.assertEqual(plant_list.json()["items"][0]["asset_id"], asset_id)

        original = self.client.get(f"/api/media/{asset_id}")
        self.assertEqual(original.status_code, 200, original.text)

    def test_media_link_remove_deletes_asset_when_last_target_is_removed(self) -> None:
        payload = self._image_bytes(fmt="PNG")
        uploaded = self.client.post(
            "/api/media/upload?target_type=plant&target_id=PLT-TEST",
            content=payload,
            headers={
                "content-type": "image/png",
                "x-upload-filename": "plant-only.png",
            },
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        asset_id = uploaded.json()["asset_id"]

        removed = self.client.delete(
            f"/api/media/{asset_id}/links?target_type=plant&target_id=PLT-TEST",
        )
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertTrue(removed.json()["deleted_asset"])

        missing = self.client.get(f"/api/media/{asset_id}")
        self.assertEqual(missing.status_code, 404, missing.text)
