import os
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import numpy as np
from fastapi import HTTPException

import gardenops.db as db
import gardenops.routers.shademap as shademap_router
from gardenops.security import create_user
from tests.base import BaseApiTest, strong_password

_PNG_HEADER_BYTES = bytes.fromhex("89504E47")
_FEATURE_BOUNDS = {
    "north": 51.5015,
    "south": 51.5004,
    "east": -0.1239,
    "west": -0.1250,
    "zoom": 17,
}
_FEATURE_BOUNDS_ALT = {
    "north": 51.5017,
    "south": 51.5006,
    "east": -0.1237,
    "west": -0.1248,
    "zoom": 17,
}
_LARGE_FEATURE_BOUNDS = {
    "north": 52.0,
    "south": 51.0,
    "east": 0.5,
    "west": -0.5,
    "zoom": 17,
}


def _complete_journey_fixture_env(artifact_dir: Path) -> dict[str, str]:
    """Return the runner-issued contract required for local provider overrides."""
    database_url = "postgresql://gardenops-test@127.0.0.1:19452/gardenops_test"
    return {
        "APP_ENV": "test",
        "DATABASE_URL": database_url,
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_ALLOW_TRUNCATE": "1",
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_ARTIFACT_DIR": str(artifact_dir),
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_CHILD": "1",
        "GARDENOPS_COMPLETE_JOURNEYS_E2E_EXPECTED_HEAD": "a" * 40,
        "GARDENOPS_DISPOSABLE_POSTGRES_MARKER": "123.fixture",
        "GARDENOPS_DISPOSABLE_POSTGRES_SYSTEM_IDENTIFIER": "123",
        "GARDENOPS_DISPOSABLE_POSTGRES_URL": database_url,
        "GARDENOPS_E2E_LOOPBACK_PROVIDER": "1",
        "GARDENOPS_E2E_PROVIDER_URL": "http://127.0.0.1:19451/v1",
    }


class TestShademap(BaseApiTest):
    def test_shademap_state_round_trip(self) -> None:
        initial = self.client.get("/api/shademap/state")
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.json()["mode"], "shadow")
        self.assertEqual(initial.json()["selected_plot_id"], None)
        self.assertEqual(initial.json()["preset"], "now")
        self.assertIsInstance(initial.json()["analysis_timestamp_ms"], int)

        updated = self.client.patch(
            "/api/shademap/state",
            json={
                "mode": "sun-hours",
                "selected_plot_id": "B2",
                "analysis_timestamp_ms": 1772443603995,
                "preset": "summer",
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(
            updated.json(),
            {
                "mode": "sun-hours",
                "selected_plot_id": "B2",
                "analysis_timestamp_ms": 1772443603995,
                "preset": "summer",
            },
        )

        fetched = self.client.get("/api/shademap/state")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json(), updated.json())

    def test_shademap_selected_plot_must_belong_to_active_garden(self) -> None:
        default_garden_id, second_garden_id, username, password = self._setup_admin_two_gardens()
        conn = db.get_db()
        try:
            user_row = conn.execute(
                "SELECT id FROM auth_users WHERE username = %s",
                (username,),
            ).fetchone()
            assert user_row is not None
            user_id = int(user_row["id"])
            conn.execute(
                """
                INSERT INTO plots (
                    plot_id, garden_id, zone_code, zone_name, plot_number,
                    grid_row, grid_col, sub_zone, notes, color
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                ("SHADE-G2", second_garden_id, "S", "Second", 1, 3, 3, "", "", None),
            )
            conn.execute(
                """
                INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                VALUES (%s, %s, %s)
                """,
                ("SHADE-G2", user_id, second_garden_id),
            )
            conn.execute(
                """
                INSERT INTO shademap_state (
                    garden_id, mode, selected_plot_id, analysis_timestamp_ms, preset
                ) VALUES (%s, 'shadow', 'SHADE-G2', 1772443603995, 'now')
                ON CONFLICT(garden_id) DO UPDATE SET selected_plot_id = excluded.selected_plot_id
                """,
                (default_garden_id,),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            client, headers = self._authenticated_client(
                username,
                password,
                garden_id=default_garden_id,
            )
            stale = client.get("/api/shademap/state", headers=headers)
            self.assertEqual(stale.status_code, 200)
            self.assertIsNone(stale.json()["selected_plot_id"])

            rejected = client.patch(
                "/api/shademap/state",
                headers=headers,
                json={
                    "mode": "shadow",
                    "selected_plot_id": "SHADE-G2",
                    "analysis_timestamp_ms": 1772443603995,
                    "preset": "now",
                },
            )
            self.assertEqual(rejected.status_code, 404)

    def test_shademap_calibration_round_trip(self) -> None:
        initial = self.client.get("/api/shademap/calibration")
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.json()["enabled"], False)

        payload = {
            "enabled": True,
            "calibration_type": "two-point",
            "origin_grid_col": 6.5,
            "origin_grid_row": 9.5,
            "origin_latitude": 51.50095,
            "origin_longitude": -0.12448,
            "axis_grid_col": 12.5,
            "axis_grid_row": 9.5,
            "axis_latitude": 51.50095,
            "axis_longitude": -0.12465,
            "house_nw_latitude": None,
            "house_nw_longitude": None,
            "house_ne_latitude": None,
            "house_ne_longitude": None,
            "house_se_latitude": None,
            "house_se_longitude": None,
            "house_sw_latitude": None,
            "house_sw_longitude": None,
        }
        updated = self.client.patch("/api/shademap/calibration", json=payload)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json(), payload)

        fetched = self.client.get("/api/shademap/calibration")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json(), payload)

    def test_invalid_house_calibration_is_rejected_without_persistence(self) -> None:
        rejected = self.client.patch(
            "/api/shademap/calibration",
            json={
                "enabled": True,
                "calibration_type": "house-corners",
                "house_nw_latitude": 51.50096,
                "house_nw_longitude": -0.12443,
                "house_ne_latitude": 51.50096,
                "house_ne_longitude": -0.12443,
                "house_se_latitude": 51.50090,
                "house_se_longitude": -0.12462,
                "house_sw_latitude": 51.50089,
                "house_sw_longitude": -0.12445,
            },
        )

        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.json()["detail"], "House calibration corners must be distinct")
        fetched = self.client.get("/api/shademap/calibration")
        self.assertEqual(fetched.status_code, 200)
        self.assertFalse(fetched.json()["enabled"])

    def test_shademap_house_corner_calibration_round_trip(self) -> None:
        payload = {
            "enabled": True,
            "calibration_type": "house-corners",
            "origin_grid_col": None,
            "origin_grid_row": None,
            "origin_latitude": None,
            "origin_longitude": None,
            "axis_grid_col": None,
            "axis_grid_row": None,
            "axis_latitude": None,
            "axis_longitude": None,
            "house_nw_latitude": 51.50096,
            "house_nw_longitude": -0.12443,
            "house_ne_latitude": 51.50097,
            "house_ne_longitude": -0.12460,
            "house_se_latitude": 51.50090,
            "house_se_longitude": -0.12462,
            "house_sw_latitude": 51.50089,
            "house_sw_longitude": -0.12445,
        }
        updated = self.client.patch("/api/shademap/calibration", json=payload)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json(), payload)

        fetched = self.client.get("/api/shademap/calibration")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json(), payload)

    def test_shademap_obstacle_crud(self) -> None:
        create_payload = {
            "label": "Apple tree",
            "kind": "tree",
            "linked_plot_id": "B1",
            "latitude": 51.50090,
            "longitude": -0.12440,
            "height_m": 4.8,
            "crown_radius_m": 2.4,
            "active": True,
        }
        created = self.client.post("/api/shademap/obstacles", json=create_payload)
        self.assertEqual(created.status_code, 201)
        obstacle_id = created.json()["id"]
        self.assertEqual(created.json()["label"], "Apple tree")

        listed = self.client.get("/api/shademap/obstacles")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)
        self.assertEqual(listed.json()[0]["id"], obstacle_id)

        update_payload = {
            **create_payload,
            "label": "Apple tree canopy",
            "height_m": 5.1,
        }
        updated = self.client.patch(
            f"/api/shademap/obstacles/{obstacle_id}",
            json=update_payload,
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["label"], "Apple tree canopy")
        self.assertEqual(updated.json()["height_m"], 5.1)

        deleted = self.client.delete(f"/api/shademap/obstacles/{obstacle_id}")
        self.assertEqual(deleted.status_code, 200)
        listed_after_delete = self.client.get("/api/shademap/obstacles")
        self.assertEqual(listed_after_delete.status_code, 200)
        self.assertEqual(listed_after_delete.json(), [])

    def test_shademap_config_uses_shademap_env_key(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "SHADEMAP": "shade-test-key",
                    "SHADEMAP_PUBLIC_API_KEY": "shade-public-key",
                    "SHADEMAP_TILE_SIGNING_SECRET": "shade-tile-secret",
                    "SHADEMAP_LAT": "51.6",
                    "SHADEMAP_LNG": "-0.2",
                    "SHADEMAP_ZOOM": "18",
                    "SHADEMAP_LABEL": "Demo house",
                    "SHADEMAP_SHARE_URL": "https://example.com/shademap",
                },
                clear=False,
            ),
            patch(
                "gardenops.routers.shademap.current_timestamp_ms",
                return_value=1_777_777_777_000,
            ),
            patch("gardenops.routers.shademap._perform_sdk_validation") as validate_mock,
            patch(
                "gardenops.routers.shademap.local_terrain_available",
                return_value=False,
            ),
        ):
            response = self.client.get("/api/shademap/config")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["api_key"], "shade-public-key")
        self.assertEqual(payload["latitude"], 51.6)
        self.assertEqual(payload["longitude"], -0.2)
        self.assertEqual(payload["zoom"], 18)
        self.assertEqual(payload["label"], "Demo house")
        self.assertEqual(payload["share_url"], "https://example.com/shademap")
        self.assertEqual(payload["terrain_max_zoom"], 15)
        self.assertEqual(payload["terrain_tile_size"], 256)
        self.assertEqual(payload["features_min_zoom"], 15)
        self.assertEqual(payload["provider_state"], "ready")
        self.assertEqual(payload["sdk_cache_status"], "miss")
        self.assertIsNone(payload["runtime_script_url"])
        self.assertEqual(
            payload["terrain_token_expires_at_ms"],
            1_777_777_777_000 + 600_000,
        )
        self.assertTrue(
            payload["terrain_url_template"].startswith("/shademap/terrain/{z}/{x}/{y}.png?token="),
        )
        token = parse_qs(urlparse(payload["terrain_url_template"]).query)["token"][0]
        token_payload = token.split(".", 1)[0]
        self.assertEqual(
            token_payload,
            f"{payload['terrain_token_expires_at_ms']}:{self._get_default_garden_id()}",
        )
        validate_mock.assert_called_once_with("shade-test-key")

    def test_shademap_config_reports_sdk_cache_hit_after_validation(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "SHADEMAP": "shade-private-key",
                    "SHADEMAP_PUBLIC_API_KEY": "shade-public-key",
                    "SHADEMAP_TILE_SIGNING_SECRET": "shade-tile-secret",
                },
                clear=False,
            ),
            patch("gardenops.routers.shademap._perform_sdk_validation") as validate_mock,
            patch("gardenops.routers.shademap.local_terrain_available", return_value=False),
        ):
            first = self.client.get("/api/shademap/config")
            second = self.client.get("/api/shademap/config")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["sdk_cache_status"], "miss")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["sdk_cache_status"], "hit")
        validate_mock.assert_called_once_with("shade-private-key")

    def test_shademap_config_uses_stale_validated_cache_on_timeout(self) -> None:
        garden_id = self._get_default_garden_id()
        cache_key = shademap_router._sdk_cache_key("shade-private-key")
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO shademap_cache (
                    garden_id, cache_kind, cache_key, fetched_at_ms,
                    content_type, payload_text
                ) VALUES (%s, 'sdk-load', %s, %s, 'text/plain', 'ok')
                """,
                (garden_id, cache_key, 1_000),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with (
            patch.dict(
                os.environ,
                {
                    "SHADEMAP": "shade-private-key",
                    "SHADEMAP_PUBLIC_API_KEY": "shade-public-key",
                    "SHADEMAP_TILE_SIGNING_SECRET": "shade-tile-secret",
                },
                clear=False,
            ),
            patch(
                "gardenops.routers.shademap.current_timestamp_ms",
                return_value=shademap_router.SDK_CACHE_TTL_MS + 2_000,
            ),
            patch(
                "gardenops.routers.shademap._perform_sdk_validation",
                side_effect=HTTPException(status_code=502, detail="Request timed out"),
            ),
            patch("gardenops.routers.shademap.local_terrain_available", return_value=False),
        ):
            response = self.client.get("/api/shademap/config")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["provider_state"], "degraded")
        self.assertEqual(response.json()["sdk_cache_status"], "stale-fallback")

    def test_shademap_config_cold_timeout_remains_retryable_error(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "SHADEMAP": "shade-cold-timeout-key",
                    "SHADEMAP_PUBLIC_API_KEY": "shade-public-key",
                    "SHADEMAP_TILE_SIGNING_SECRET": "shade-tile-secret",
                },
                clear=False,
            ),
            patch(
                "gardenops.routers.shademap._perform_sdk_validation",
                side_effect=HTTPException(status_code=502, detail="Request timed out"),
            ),
        ):
            response = self.client.get("/api/shademap/config")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"], "Request timed out")

    def test_shademap_config_does_not_use_stale_cache_for_invalid_key(self) -> None:
        garden_id = self._get_default_garden_id()
        cache_key = shademap_router._sdk_cache_key("shade-invalid-key")
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO shademap_cache (
                    garden_id, cache_kind, cache_key, fetched_at_ms,
                    content_type, payload_text
                ) VALUES (%s, 'sdk-load', %s, 1000, 'text/plain', 'ok')
                """,
                (garden_id, cache_key),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with (
            patch.dict(
                os.environ,
                {
                    "SHADEMAP": "shade-invalid-key",
                    "SHADEMAP_PUBLIC_API_KEY": "shade-public-key",
                    "SHADEMAP_TILE_SIGNING_SECRET": "shade-tile-secret",
                },
                clear=False,
            ),
            patch(
                "gardenops.routers.shademap.current_timestamp_ms",
                return_value=shademap_router.SDK_CACHE_TTL_MS + 2_000,
            ),
            patch(
                "gardenops.routers.shademap._perform_sdk_validation",
                side_effect=HTTPException(status_code=503, detail="API key invalid"),
            ),
        ):
            response = self.client.get("/api/shademap/config")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "API key invalid")

    def test_shademap_config_requires_dedicated_tile_signing_secret(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "SHADEMAP": "shade-private-key",
                    "SHADEMAP_PUBLIC_API_KEY": "shade-public-key",
                    "SHADEMAP_TILE_SIGNING_SECRET": "",
                    "AUTH_API_KEY": "shared-auth-fallback-not-allowed",
                },
                clear=False,
            ),
            patch("gardenops.routers.shademap._perform_sdk_validation"),
            patch(
                "gardenops.routers.shademap.local_terrain_available",
                return_value=False,
            ),
        ):
            response = self.client.get("/api/shademap/config")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "SHADEMAP_TILE_SIGNING_SECRET not configured")

    def test_shademap_config_rejects_public_placeholder_tile_signing_secret(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "SHADEMAP": "shade-private-key",
                    "SHADEMAP_PUBLIC_API_KEY": "shade-public-key",
                    "SHADEMAP_TILE_SIGNING_SECRET": "change-me",
                },
                clear=False,
            ),
            patch("gardenops.routers.shademap._perform_sdk_validation"),
            patch(
                "gardenops.routers.shademap.local_terrain_available",
                return_value=False,
            ),
        ):
            response = self.client.get("/api/shademap/config")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "SHADEMAP_TILE_SIGNING_SECRET not configured")

    def test_shademap_config_requires_api_key(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SHADEMAP": "",
                "SHADEMAP_API_KEY": "",
                "SHADEMAP_KEY": "",
                "SHADEMAP_PUBLIC_API_KEY": "",
                "SHADEMAP_PUBLIC_KEY": "",
                "SHADEMAP_CLIENT_KEY": "",
            },
            clear=False,
        ):
            response = self.client.get("/api/shademap/config")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "SHADEMAP public API key not configured")

    def test_shademap_config_does_not_expose_private_key_when_public_missing(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "SHADEMAP": "shade-private-only-key",
                    "SHADEMAP_PUBLIC_API_KEY": "",
                    "SHADEMAP_PUBLIC_KEY": "",
                    "SHADEMAP_CLIENT_KEY": "",
                },
                clear=False,
            ),
            patch("gardenops.routers.shademap._perform_sdk_validation"),
        ):
            response = self.client.get("/api/shademap/config")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "SHADEMAP public API key not configured")

    def test_shademap_loopback_sdk_fixture_url_is_test_only_and_adapter_specific(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture_env = _complete_journey_fixture_env(Path(tmp))
            with patch.dict(os.environ, fixture_env, clear=False):
                self.assertEqual(
                    shademap_router._loopback_sdk_validation_url(),
                    "http://127.0.0.1:19451/shademap/sdk/load",
                )
                self.assertEqual(
                    shademap_router._runtime_script_upstream_url(),
                    "http://127.0.0.1:19451/shademap/runtime.js",
                )

            invalid_urls = (
                "",
                "https://127.0.0.1:19451/v1",
                "http://localhost:19451/v1",
                "http://127.0.0.1/v1",
                "http://127.0.0.1:0/v1",
                "http://127.0.0.1:5432/v1",
                "http://127.0.0.1:19451",
                "http://127.0.0.1:19451/v1/",
                "http://127.0.0.1:19451/not-v1",
                "http://127.0.0.1:not-a-port/v1",
                "http://user:pass@127.0.0.1:19451/v1",
                "http://127.0.0.1:19451/v1?scenario=success",
                "http://127.0.0.1:19451/v1#fragment",
            )
            for value in invalid_urls:
                with (
                    self.subTest(value=value),
                    patch.dict(
                        os.environ,
                        {**fixture_env, "GARDENOPS_E2E_PROVIDER_URL": value},
                        clear=False,
                    ),
                ):
                    with self.assertRaises(HTTPException) as raised:
                        shademap_router._loopback_sdk_validation_url()
                    self.assertEqual(raised.exception.status_code, 503)
                    self.assertEqual(
                        raised.exception.detail,
                        "Invalid ShadeMap loopback fixture URL",
                    )

            with patch.dict(
                os.environ,
                {**fixture_env, "APP_ENV": "production"},
                clear=False,
            ):
                self.assertIsNone(shademap_router._loopback_sdk_validation_url())
            with patch.dict(
                os.environ,
                {**fixture_env, "GARDENOPS_COMPLETE_JOURNEYS_E2E_CHILD": ""},
                clear=False,
            ):
                self.assertIsNone(shademap_router._loopback_sdk_validation_url())

    def test_shademap_sdk_validation_posts_to_loopback_fixture_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.dict(
                    os.environ,
                    _complete_journey_fixture_env(Path(tmp)),
                    clear=False,
                ),
                patch(
                    "gardenops.routers.shademap._request_validated_bytes",
                    return_value=(b"{}", "application/json"),
                ) as fixture_request,
                patch("gardenops.routers.shademap._request_bytes") as production_request,
            ):
                shademap_router._perform_sdk_validation("shade-test-key")

            fixture_request.assert_called_once_with(
                "http://127.0.0.1:19451/shademap/sdk/load",
                method="POST",
                body=b'{"api_key": "shade-test-key"}',
                headers={"Content-Type": "application/json"},
                timeout=15.0,
            )
            production_request.assert_not_called()

    def test_shademap_runtime_script_is_authenticated_and_proxy_served(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.dict(
                    os.environ,
                    _complete_journey_fixture_env(Path(tmp)),
                    clear=False,
                ),
                patch(
                    "gardenops.routers.shademap._request_validated_bytes",
                    return_value=(b"window.GardenOpsShadeMap = function () {};", "text/javascript"),
                ) as fixture_request,
            ):
                response = self.client.get("/shademap/runtime.js")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], "application/javascript")
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")
            self.assertIn("GardenOpsShadeMap", response.text)
            fixture_request.assert_called_once_with(
                "http://127.0.0.1:19451/shademap/runtime.js",
                timeout=20,
            )

    def test_shademap_sdk_validation_ignores_loopback_override_in_production(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "APP_ENV": "production",
                    "GARDENOPS_E2E_LOOPBACK_PROVIDER": "1",
                    "GARDENOPS_E2E_PROVIDER_URL": "https://attacker.invalid/v1?ignored=1",
                },
                clear=False,
            ),
            patch(
                "gardenops.routers.shademap._request_bytes",
                return_value=(b"{}", "application/json"),
            ) as production_request,
            patch("gardenops.routers.shademap._request_validated_bytes") as fixture_request,
        ):
            shademap_router._perform_sdk_validation("shade-test-key")

        production_request.assert_called_once_with(
            shademap_router.SDK_LOAD_URL,
            method="POST",
            body=b'{"api_key": "shade-test-key"}',
            headers={"Content-Type": "application/json"},
            timeout=15.0,
        )
        fixture_request.assert_not_called()

    def test_shademap_monthly_estimated_sun_aggregates_csv_by_month(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "soltider_estimated.csv"
            csv_path.write_text(
                "\n".join(
                    [
                        "dag;dato;sol_opp;sol_ned;timer_sol;estimert;kommentar",
                        "1;2024-01-01;;;1,00;TRUE;",
                        "2;2024-01-02;;;3,00;TRUE;",
                        "3;2024-02-01;;;2,50;TRUE;",
                        "4;2024-02-02;;;3,50;TRUE;",
                    ],
                ),
                encoding="utf-8",
            )

            with patch("gardenops.routers.shademap.MONTHLY_ESTIMATE_CSV_PATH", csv_path):
                response = self.client.get("/api/shademap/monthly-estimated-sun")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source_name"], "soltider_estimated.csv")
        self.assertEqual(payload["source_date_start"], "2024-01-01")
        self.assertEqual(payload["source_date_end"], "2024-02-02")
        self.assertEqual(len(payload["values"]), 12)
        self.assertEqual(payload["values"][0]["month_label"], "Jan")
        self.assertAlmostEqual(payload["values"][0]["hours"], 2.0)
        self.assertEqual(payload["values"][0]["sample_days"], 2)
        self.assertEqual(payload["values"][1]["month_label"], "Feb")
        self.assertAlmostEqual(payload["values"][1]["hours"], 3.0)
        self.assertEqual(payload["values"][1]["sample_days"], 2)
        self.assertAlmostEqual(payload["values"][2]["hours"], 0.0)
        self.assertEqual(payload["values"][2]["sample_days"], 0)

    def test_loopback_monthly_estimate_fixture_requires_explicit_test_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact_dir = Path(tmp)
            fixture_path = artifact_dir / "phase-seven-sun.csv"
            fixture_path.write_text(
                "dag;dato;sol_opp;sol_ned;timer_sol;estimert;kommentar\n"
                "1;2026-07-12;04:00;22:00;18,00;TRUE;fixture\n",
                encoding="utf-8",
            )
            fixture_env = {
                **_complete_journey_fixture_env(artifact_dir),
                "GARDENOPS_E2E_SHADEMAP_ESTIMATE_CSV": str(fixture_path),
            }
            with patch.dict(os.environ, fixture_env, clear=False):
                self.assertEqual(shademap_router._monthly_estimate_csv_path(), fixture_path)

            with patch.dict(
                os.environ,
                {**fixture_env, "APP_ENV": "production"},
                clear=False,
            ):
                self.assertEqual(
                    shademap_router._monthly_estimate_csv_path(),
                    shademap_router.MONTHLY_ESTIMATE_CSV_PATH,
                )

    def test_shademap_feature_results_are_cached_in_db(self) -> None:
        expected = [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-0.12410, 51.50060],
                            [-0.12490, 51.50060],
                            [-0.12490, 51.50130],
                            [-0.12410, 51.50130],
                            [-0.12410, 51.50060],
                        ]
                    ],
                },
                "properties": {
                    "height": 8.0,
                    "render_height": 8.0,
                    "name": "House",
                    "source_id": "way/1",
                },
            },
        ]
        params = dict(_FEATURE_BOUNDS)

        with patch(
            "gardenops.routers.shademap._fetch_overpass_features",
            return_value=expected,
        ) as fetch_mock:
            first = self.client.get("/api/shademap/features", params=params)
            second = self.client.get("/api/shademap/features", params=params)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        first_features = first.json()["features"]
        second_features = second.json()["features"]
        self.assertEqual(first_features, second_features)
        self.assertEqual(fetch_mock.call_count, 1)
        source_ids = [f["properties"]["source_id"] for f in first_features]
        self.assertNotIn(
            "way/1",
            source_ids,
            "Overlapping Overpass building should be replaced",
        )
        self.assertIn(
            "gardenops-house",
            source_ids,
            "Planner house should replace Overpass building",
        )

    def test_shademap_features_include_planner_house_when_upstream_has_no_house(self) -> None:
        params = dict(_FEATURE_BOUNDS)

        with patch(
            "gardenops.routers.shademap._fetch_overpass_features",
            return_value=[],
        ) as fetch_mock:
            first = self.client.get("/api/shademap/features", params=params)
            second = self.client.get("/api/shademap/features", params=params)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(fetch_mock.call_count, 1)
        first_features = first.json()["features"]
        second_features = second.json()["features"]
        self.assertEqual(first_features, second_features)
        self.assertEqual(len(first_features), 1)
        planner_house = first_features[0]
        self.assertEqual(planner_house["geometry"]["type"], "Polygon")
        self.assertEqual(planner_house["properties"]["source_id"], "gardenops-house")
        self.assertEqual(planner_house["properties"]["name"], "Planner house")
        self.assertGreater(planner_house["properties"]["render_height"], 0)

    def test_shademap_features_rejects_excessive_bbox_tiles(self) -> None:
        params = dict(_LARGE_FEATURE_BOUNDS)
        with patch.dict(
            os.environ,
            {"SHADEMAP_FEATURES_MAX_BBOX_TILES": "1"},
            clear=False,
        ):
            response = self.client.get("/api/shademap/features", params=params)
        self.assertEqual(response.status_code, 400)
        self.assertIn("bounds are too large", response.json()["detail"])

    def test_shademap_features_distinct_bounds_budget_enforced(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "SHADEMAP_FEATURES_MAX_DISTINCT_BOUNDS": "1",
                    "SHADEMAP_FEATURES_DISTINCT_WINDOW_SECONDS": "600",
                },
                clear=False,
            ),
            patch(
                "gardenops.routers.shademap._fetch_overpass_features",
                return_value=[],
            ),
        ):
            first = self.client.get(
                "/api/shademap/features",
                params=_FEATURE_BOUNDS,
            )
            second = self.client.get(
                "/api/shademap/features",
                params=_FEATURE_BOUNDS_ALT,
            )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)

    def test_shademap_features_daily_budget_enforced_on_miss(self) -> None:
        self._create_test_user("feature_budget_user", "feature-budget-pass", role="editor")

        with (
            patch.dict(
                os.environ,
                {
                    "AUTH_REQUIRED": "true",
                    "AUTH_MODE": "session",
                    "AUTH_API_KEY": "",
                    "SHADEMAP_FEATURES_MISS_DAILY_BUDGET_USER": "1",
                    "SHADEMAP_FEATURES_MISS_DAILY_BUDGET_GARDEN": "5",
                    "SHADEMAP_FEATURES_MAX_DISTINCT_BOUNDS": "10",
                },
                clear=False,
            ),
            patch(
                "gardenops.routers.shademap._fetch_overpass_features",
                return_value=[],
            ),
        ):
            client = self._new_client()
            _, csrf = self._login_session(
                "feature_budget_user",
                "feature-budget-pass",
                client=client,
            )
            headers = self._session_headers(csrf)
            first = client.get(
                "/api/shademap/features",
                headers=headers,
                params=_FEATURE_BOUNDS,
            )
            second = client.get(
                "/api/shademap/features",
                headers=headers,
                params=_FEATURE_BOUNDS_ALT,
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertIn("daily budget exhausted", second.json()["detail"])

    def test_shademap_features_fall_back_to_planner_house_when_overpass_fails(self) -> None:
        params = dict(_FEATURE_BOUNDS)

        with patch(
            "gardenops.routers.shademap._fetch_overpass_features",
            side_effect=HTTPException(status_code=502, detail="Overpass down"),
        ):
            response = self.client.get("/api/shademap/features", params=params)

        self.assertEqual(response.status_code, 200)
        features = response.json()["features"]
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]["properties"]["source_id"], "gardenops-house")

    def test_shademap_features_fall_back_to_planner_house_when_overpass_times_out(self) -> None:
        params = dict(_FEATURE_BOUNDS)

        with patch(
            "gardenops.routers.shademap._request_bytes",
            side_effect=HTTPException(status_code=502, detail="timed out"),
        ):
            response = self.client.get("/api/shademap/features", params=params)

        self.assertEqual(response.status_code, 200)
        features = response.json()["features"]
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]["properties"]["source_id"], "gardenops-house")

    def test_shademap_features_include_db_tree_canopies(self) -> None:
        conn = db.get_db()
        try:
            conn.execute(
                """
                INSERT INTO plants (
                    plt_id, name, latin, category, bloom_month, color,
                    hardiness, height_cm, light, link
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    "PLT-TREE",
                    "Apple Tree",
                    "Malus domestica",
                    "trær",
                    "",
                    "",
                    "H4",
                    650,
                    "sol",
                    "",
                ),
            )
            conn.execute(
                """
                INSERT INTO plot_plants (plot_id, plt_id, quantity)
                VALUES ('B1', 'PLT-TREE', 1)
                """,
            )
            conn.commit()

            params = dict(_FEATURE_BOUNDS)

            with patch(
                "gardenops.routers.shademap._fetch_overpass_features",
                return_value=[],
            ):
                response = self.client.get("/api/shademap/features", params=params)

            self.assertEqual(response.status_code, 200)
            features = response.json()["features"]
            source_ids = {feature["properties"]["source_id"] for feature in features}
            self.assertIn("gardenops-house", source_ids)
            self.assertIn("gardenops-tree:B1", source_ids)
            tree_feature = next(
                feature
                for feature in features
                if feature["properties"]["source_id"] == "gardenops-tree:B1"
            )
            self.assertEqual(tree_feature["geometry"]["type"], "Polygon")
            self.assertGreaterEqual(tree_feature["properties"]["render_height"], 6.5)
        finally:
            conn.execute("DELETE FROM plot_plants WHERE plt_id = 'PLT-TREE'")
            conn.execute("DELETE FROM plants WHERE plt_id = 'PLT-TREE'")
            conn.commit()
            db.return_db(conn)

    def test_shademap_features_include_manual_db_obstacles(self) -> None:
        default_garden_id = self._get_default_garden_id()
        conn = db.get_db()
        cursor = conn.execute(
            """
            INSERT INTO shademap_obstacles (
                label,
                kind,
                linked_plot_id,
                latitude,
                longitude,
                height_m,
                crown_radius_m,
                active,
                garden_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            ("Pear tree", "tree", "B1", 51.50090, -0.12442, 5.5, 2.1, 1, default_garden_id),
        )
        obstacle_id = cursor.fetchone()["id"]
        conn.commit()
        db.return_db(conn)

        params = dict(_FEATURE_BOUNDS)
        with patch(
            "gardenops.routers.shademap._fetch_overpass_features",
            return_value=[],
        ):
            response = self.client.get("/api/shademap/features", params=params)

        self.assertEqual(response.status_code, 200)
        source_ids = {feature["properties"]["source_id"] for feature in response.json()["features"]}
        self.assertIn(f"gardenops-obstacle:{obstacle_id}", source_ids)
        self.assertNotIn("gardenops-tree:B1", source_ids)

    def test_shademap_terrain_tiles_are_cached_in_db(self) -> None:
        with (
            patch("gardenops.routers.shademap._perform_sdk_validation"),
            patch(
                "gardenops.routers.shademap.local_terrain_available",
                return_value=False,
            ),
        ):
            config = self.client.get("/api/shademap/config")
        self.assertEqual(config.status_code, 200)
        template = config.json()["terrain_url_template"]
        token = parse_qs(urlparse(template).query)["token"][0]

        with (
            patch(
                "gardenops.routers.shademap._request_bytes",
                return_value=(b"png-bytes", "image/png"),
            ) as fetch_mock,
            patch(
                "gardenops.routers.shademap.sample_local_terrain_tile",
                return_value=None,
            ),
            patch(
                "gardenops.routers.shademap.local_terrain_signature",
                return_value=None,
            ),
        ):
            first = self.client.get("/shademap/terrain/15/17432/9123.png", params={"token": token})
            second = self.client.get("/shademap/terrain/15/17432/9123.png", params={"token": token})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.content, b"png-bytes")
        self.assertEqual(second.content, b"png-bytes")
        self.assertEqual(first.headers["content-type"], "image/png")
        self.assertIn("private", first.headers["cache-control"])
        self.assertNotIn("public", first.headers["cache-control"])
        self.assertIn("private", second.headers["cache-control"])
        self.assertNotIn("public", second.headers["cache-control"])
        self.assertEqual(fetch_mock.call_count, 1)

    def test_shademap_terrain_distinct_tile_budget_enforced(self) -> None:
        with (
            patch("gardenops.routers.shademap._perform_sdk_validation"),
            patch(
                "gardenops.routers.shademap.local_terrain_available",
                return_value=False,
            ),
        ):
            config = self.client.get("/api/shademap/config")
        self.assertEqual(config.status_code, 200)
        signed_tile_token = parse_qs(
            urlparse(config.json()["terrain_url_template"]).query,
        )["token"][0]

        with (
            patch.dict(
                os.environ,
                {
                    "SHADEMAP_TERRAIN_MAX_DISTINCT_TILES": "1",
                    "SHADEMAP_TERRAIN_DISTINCT_WINDOW_SECONDS": "600",
                },
                clear=False,
            ),
            patch(
                "gardenops.routers.shademap._request_bytes",
                return_value=(b"png-bytes", "image/png"),
            ),
            patch(
                "gardenops.routers.shademap.sample_local_terrain_tile",
                return_value=None,
            ),
            patch(
                "gardenops.routers.shademap.local_terrain_signature",
                return_value=None,
            ),
            patch(
                "gardenops.routers.shademap._house_overlaps_tile",
                return_value=False,
            ),
        ):
            first = self.client.get(
                f"/shademap/terrain/1/0/0.png?token={signed_tile_token}",
            )
            second = self.client.get(
                f"/shademap/terrain/1/1/0.png?token={signed_tile_token}",
            )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)

    def test_shademap_terrain_miss_rate_limit_enforced(self) -> None:
        with (
            patch("gardenops.routers.shademap._perform_sdk_validation"),
            patch(
                "gardenops.routers.shademap.local_terrain_available",
                return_value=False,
            ),
        ):
            config = self.client.get("/api/shademap/config")
        self.assertEqual(config.status_code, 200)
        signed_tile_token = parse_qs(
            urlparse(config.json()["terrain_url_template"]).query,
        )["token"][0]

        with (
            patch.dict(
                os.environ,
                {
                    "SHADEMAP_TERRAIN_MISS_RATE_LIMIT": "1",
                    "SHADEMAP_TERRAIN_MAX_DISTINCT_TILES": "9999",
                },
                clear=False,
            ),
            patch(
                "gardenops.routers.shademap._request_bytes",
                return_value=(b"png-bytes", "image/png"),
            ),
            patch(
                "gardenops.routers.shademap.sample_local_terrain_tile",
                return_value=None,
            ),
            patch(
                "gardenops.routers.shademap.local_terrain_signature",
                return_value=None,
            ),
            patch(
                "gardenops.routers.shademap._house_overlaps_tile",
                return_value=False,
            ),
        ):
            first = self.client.get(
                f"/shademap/terrain/1/0/0.png?token={signed_tile_token}",
            )
            second = self.client.get(
                f"/shademap/terrain/1/1/0.png?token={signed_tile_token}",
            )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)

    def test_shademap_terrain_rejects_non_image_remote_content(self) -> None:
        with (
            patch("gardenops.routers.shademap._perform_sdk_validation"),
            patch(
                "gardenops.routers.shademap.local_terrain_available",
                return_value=False,
            ),
        ):
            config = self.client.get("/api/shademap/config")
        self.assertEqual(config.status_code, 200)
        signed_tile_token = parse_qs(
            urlparse(config.json()["terrain_url_template"]).query,
        )["token"][0]

        with (
            patch(
                "gardenops.routers.shademap._request_bytes",
                return_value=(b"<script></script>", "text/html"),
            ),
            patch("gardenops.routers.shademap.sample_local_terrain_tile", return_value=None),
            patch("gardenops.routers.shademap.local_terrain_signature", return_value=None),
            patch("gardenops.routers.shademap._house_overlaps_tile", return_value=False),
        ):
            response = self.client.get(
                f"/shademap/terrain/1/0/0.png?token={signed_tile_token}",
            )

        self.assertEqual(response.status_code, 502, response.text)
        self.assertIn("terrain", response.json()["detail"].lower())

    def test_shademap_terrain_daily_budget_enforced_on_remote_miss(self) -> None:
        self._create_test_user("terrain_budget_user", "terrain-budget-pass", role="editor")

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "SHADEMAP_TERRAIN_MISS_DAILY_BUDGET_USER": "1",
                "SHADEMAP_TERRAIN_MISS_DAILY_BUDGET_GARDEN": "5",
                "SHADEMAP_TERRAIN_MAX_DISTINCT_TILES": "9999",
            },
            clear=False,
        ):
            client = self._new_client()
            _, csrf = self._login_session(
                "terrain_budget_user",
                "terrain-budget-pass",
                client=client,
            )
            headers = self._session_headers(csrf)

            with (
                patch("gardenops.routers.shademap._perform_sdk_validation"),
                patch(
                    "gardenops.routers.shademap.local_terrain_available",
                    return_value=False,
                ),
            ):
                config = client.get("/api/shademap/config", headers=headers)
            self.assertEqual(config.status_code, 200)
            token = parse_qs(urlparse(config.json()["terrain_url_template"]).query)["token"][0]

            with (
                patch(
                    "gardenops.routers.shademap._request_bytes",
                    return_value=(b"png-bytes", "image/png"),
                ),
                patch(
                    "gardenops.routers.shademap.sample_local_terrain_tile",
                    return_value=None,
                ),
                patch(
                    "gardenops.routers.shademap.local_terrain_signature",
                    return_value=None,
                ),
                patch(
                    "gardenops.routers.shademap._house_overlaps_tile",
                    return_value=False,
                ),
            ):
                first = client.get(
                    f"/shademap/terrain/1/0/0.png?token={token}",
                    headers=headers,
                )
                second = client.get(
                    f"/shademap/terrain/1/1/0.png?token={token}",
                    headers=headers,
                )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertIn("daily budget exhausted", second.json()["detail"])

    def test_shademap_terrain_rejects_invalid_tile_coordinates(self) -> None:
        with (
            patch("gardenops.routers.shademap._perform_sdk_validation"),
            patch(
                "gardenops.routers.shademap.local_terrain_available",
                return_value=False,
            ),
        ):
            config = self.client.get("/api/shademap/config")
        self.assertEqual(config.status_code, 200)
        token = parse_qs(urlparse(config.json()["terrain_url_template"]).query)["token"][0]

        with patch("gardenops.routers.shademap._request_bytes") as fetch_mock:
            zoom_too_high = self.client.get("/shademap/terrain/23/0/0.png", params={"token": token})
            x_too_high = self.client.get(
                "/shademap/terrain/15/32768/0.png", params={"token": token}
            )
            y_too_high = self.client.get(
                "/shademap/terrain/15/0/32768.png", params={"token": token}
            )

        self.assertEqual(zoom_too_high.status_code, 404)
        self.assertEqual(x_too_high.status_code, 404)
        self.assertEqual(y_too_high.status_code, 404)
        fetch_mock.assert_not_called()

    def test_shademap_terrain_prefers_local_lidar_tile_when_available(self) -> None:
        with (
            patch("gardenops.routers.shademap._perform_sdk_validation"),
            patch(
                "gardenops.routers.shademap.local_terrain_available",
                return_value=True,
            ),
        ):
            config = self.client.get("/api/shademap/config")
        self.assertEqual(config.status_code, 200)
        self.assertEqual(config.json()["terrain_max_zoom"], 18)
        token = parse_qs(urlparse(config.json()["terrain_url_template"]).query)["token"][0]

        local_png = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\rIDATx\x9cc`\x00\x02\x00\x00\x05\x00\x01\r\n-\xb4"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        sample = type(
            "LocalTile",
            (),
            {
                "fully_covered": True,
                "elevations": np.full((256, 256), 30.0),
            },
        )()

        with (
            patch(
                "gardenops.routers.shademap.sample_local_terrain_tile",
                return_value=sample,
            ),
            patch(
                "gardenops.routers.shademap.encode_terrarium_png",
                return_value=local_png,
            ) as encode_mock,
            patch(
                "gardenops.routers.shademap.local_terrain_signature",
                return_value="lidar-test",
            ),
            patch(
                "gardenops.routers.shademap._request_bytes",
            ) as fetch_mock,
        ):
            first = self.client.get(
                "/shademap/terrain/18/140000/74000.png", params={"token": token}
            )
            second = self.client.get(
                "/shademap/terrain/18/140000/74000.png", params={"token": token}
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.content, local_png)
        self.assertEqual(second.content, local_png)
        self.assertEqual(encode_mock.call_count, 1)
        fetch_mock.assert_not_called()

    def test_shademap_terrain_stamps_house_on_remote_tiles_when_overlapping(self) -> None:
        with (
            patch("gardenops.routers.shademap._perform_sdk_validation"),
            patch(
                "gardenops.routers.shademap.local_terrain_available",
                return_value=False,
            ),
        ):
            config = self.client.get("/api/shademap/config")
        self.assertEqual(config.status_code, 200)
        token = parse_qs(urlparse(config.json()["terrain_url_template"]).query)["token"][0]

        remote_png = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\rIDATx\x9cc`\x00\x02\x00\x00\x05\x00\x01\r\n-\xb4"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        remote_elevations = np.full((256, 256), 45.0, dtype=np.float32)
        stamped_elevations = np.full((256, 256), 54.0, dtype=np.float32)

        with (
            patch(
                "gardenops.routers.shademap.sample_local_terrain_tile",
                return_value=None,
            ),
            patch(
                "gardenops.routers.shademap.local_terrain_signature",
                return_value=None,
            ),
            patch(
                "gardenops.routers.shademap._request_bytes",
                return_value=(remote_png, "image/png"),
            ),
            patch(
                "gardenops.routers.shademap._house_overlaps_tile",
                return_value=True,
            ),
            patch(
                "gardenops.routers.shademap.decode_terrarium_png",
                return_value=remote_elevations,
            ) as decode_mock,
            patch(
                "gardenops.routers.shademap._apply_house_to_terrain",
                return_value=stamped_elevations,
            ) as stamp_mock,
            patch(
                "gardenops.routers.shademap.encode_terrarium_png",
                return_value=b"stamped-png",
            ) as encode_mock,
        ):
            response = self.client.get(
                "/shademap/terrain/15/17432/9123.png",
                params={"token": token},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"stamped-png")
        self.assertEqual(response.headers["content-type"], "image/png")
        decode_mock.assert_called_once()
        stamp_mock.assert_called_once()
        encode_mock.assert_called_once_with(stamped_elevations)

    def test_shademap_state_calibration_and_obstacles_are_garden_scoped(self) -> None:
        default_garden_id, second_garden_id, username, password = self._setup_admin_two_gardens()
        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            _, csrf = self._login_session(username, password)
            default_headers = self._session_headers(csrf, garden_id=default_garden_id)
            second_headers = self._session_headers(csrf, garden_id=second_garden_id)

            state_default = self.client.patch(
                "/api/shademap/state",
                headers=default_headers,
                json={
                    "mode": "shadow",
                    "selected_plot_id": None,
                    "analysis_timestamp_ms": 1772443603995,
                    "preset": "spring",
                },
            )
            self.assertEqual(state_default.status_code, 200)

            state_second = self.client.patch(
                "/api/shademap/state",
                headers=second_headers,
                json={
                    "mode": "sun-hours",
                    "selected_plot_id": None,
                    "analysis_timestamp_ms": 1772443604995,
                    "preset": "winter",
                },
            )
            self.assertEqual(state_second.status_code, 200)

            calibration_second = self.client.patch(
                "/api/shademap/calibration",
                headers=second_headers,
                json={
                    "enabled": True,
                    "origin_grid_col": 6.5,
                    "origin_grid_row": 9.5,
                    "origin_latitude": 51.50095,
                    "origin_longitude": -0.12448,
                    "axis_grid_col": 12.5,
                    "axis_grid_row": 9.5,
                    "axis_latitude": 51.50095,
                    "axis_longitude": -0.12465,
                },
            )
            self.assertEqual(calibration_second.status_code, 200)
            self.assertTrue(calibration_second.json()["enabled"])

            obstacle_second = self.client.post(
                "/api/shademap/obstacles",
                headers=second_headers,
                json={
                    "label": "Second garden tree",
                    "kind": "tree",
                    "linked_plot_id": None,
                    "latitude": 51.50091,
                    "longitude": -0.12439,
                    "height_m": 4.8,
                    "crown_radius_m": 1.8,
                    "active": True,
                },
            )
            self.assertEqual(obstacle_second.status_code, 201)

            default_state = self.client.get("/api/shademap/state", headers=default_headers)
            self.assertEqual(default_state.status_code, 200)
            self.assertEqual(default_state.json()["preset"], "spring")

            second_state = self.client.get("/api/shademap/state", headers=second_headers)
            self.assertEqual(second_state.status_code, 200)
            self.assertEqual(second_state.json()["preset"], "winter")

            default_cal = self.client.get("/api/shademap/calibration", headers=default_headers)
            self.assertEqual(default_cal.status_code, 200)
            self.assertFalse(default_cal.json()["enabled"])

            second_cal = self.client.get("/api/shademap/calibration", headers=second_headers)
            self.assertEqual(second_cal.status_code, 200)
            self.assertTrue(second_cal.json()["enabled"])

            default_obstacles = self.client.get("/api/shademap/obstacles", headers=default_headers)
            self.assertEqual(default_obstacles.status_code, 200)
            self.assertEqual(default_obstacles.json(), [])

            second_obstacles = self.client.get("/api/shademap/obstacles", headers=second_headers)
            self.assertEqual(second_obstacles.status_code, 200)
            self.assertEqual(len(second_obstacles.json()), 1)
            self.assertEqual(second_obstacles.json()[0]["label"], "Second garden tree")

    def test_plot_elevation_cache_purge_is_scoped_to_selected_garden(self) -> None:
        default_garden_id, second_garden_id, username, password = self._setup_admin_two_gardens()
        with patch.dict(
            os.environ,
            {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""},
            clear=False,
        ):
            _, csrf = self._login_session(username, password)
            default_headers = self._session_headers(csrf, garden_id=default_garden_id)
            second_headers = self._session_headers(csrf, garden_id=second_garden_id)

            create_default_plot = self.client.post(
                "/api/plots",
                headers=default_headers,
                json={
                    "plot_id": "EGD",
                    "zone_code": "E",
                    "zone_name": "Elev",
                    "plot_number": 1,
                    "grid_row": 20,
                    "grid_col": 20,
                },
            )
            self.assertEqual(create_default_plot.status_code, 201)

            create_second_plot = self.client.post(
                "/api/plots",
                headers=second_headers,
                json={
                    "plot_id": "EGS",
                    "zone_code": "E",
                    "zone_name": "Elev",
                    "plot_number": 2,
                    "grid_row": 21,
                    "grid_col": 21,
                },
            )
            self.assertEqual(create_second_plot.status_code, 201)

            conn = db.get_db()
            try:
                conn.execute(
                    """
                    INSERT INTO plot_elevations (plot_id, elevation_m, cache_sig, garden_id)
                    VALUES ('EGD', 42.0, 'sig-default', %s) ON CONFLICT DO NOTHING
                    """,
                    (default_garden_id,),
                )
                conn.execute(
                    """
                    INSERT INTO plot_elevations (plot_id, elevation_m, cache_sig, garden_id)
                    VALUES ('EGS', 43.0, 'sig-second', %s) ON CONFLICT DO NOTHING
                    """,
                    (second_garden_id,),
                )
                conn.execute(
                    """
                    INSERT INTO shademap_cache (
                        garden_id, cache_kind, cache_key, fetched_at_ms, content_type, payload_blob
                    )
                    VALUES (%s, 'terrain-tile', 'g:default:terrain', 0, 'image/png', %s)
                    """,
                    (default_garden_id, _PNG_HEADER_BYTES),
                )
                conn.execute(
                    """
                    INSERT INTO shademap_cache (
                        garden_id, cache_kind, cache_key, fetched_at_ms, content_type, payload_blob
                    )
                    VALUES (%s, 'terrain-tile', 'g:second:terrain', 0, 'image/png', %s)
                    """,
                    (second_garden_id, _PNG_HEADER_BYTES),
                )
                conn.commit()
            finally:
                db.return_db(conn)

            patch_second = self.client.patch(
                "/api/plots/elevations",
                headers=second_headers,
                json={"overrides": {"EGS": 45.0}},
            )
            self.assertEqual(patch_second.status_code, 200)

            conn = db.get_db()
            try:
                default_cache_row = conn.execute(
                    """
                    SELECT 1
                    FROM shademap_cache
                    WHERE garden_id = %s AND cache_kind = 'terrain-tile'
                      AND cache_key = 'g:default:terrain'
                    """,
                    (default_garden_id,),
                ).fetchone()
                self.assertIsNotNone(default_cache_row)

                second_cache_row = conn.execute(
                    """
                    SELECT 1
                    FROM shademap_cache
                    WHERE garden_id = %s AND cache_kind = 'terrain-tile'
                      AND cache_key = 'g:second:terrain'
                    """,
                    (second_garden_id,),
                ).fetchone()
                self.assertIsNone(second_cache_row)
            finally:
                db.return_db(conn)

    def test_security_metrics_and_alerts_include_shademap_miss_spikes(self) -> None:
        conn = db.get_db()
        try:
            create_user(
                conn,
                username="shade_metrics_admin",
                password=strong_password("adminpass123"),
                role="admin",
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with patch.dict(
            os.environ,
            {
                "AUTH_REQUIRED": "true",
                "AUTH_MODE": "session",
                "AUTH_API_KEY": "",
                "ALERT_SHADEMAP_FEATURES_CACHE_MISSES_PER_5M": "1",
                "ALERT_SHADEMAP_FEATURES_CACHE_MISS_RATIO_PCT": "50",
                "ALERT_SHADEMAP_TERRAIN_REMOTE_MISSES_PER_5M": "1",
                "ALERT_SHADEMAP_TERRAIN_REMOTE_MISS_RATIO_PCT": "50",
                "SHADEMAP_FEATURES_MAX_DISTINCT_BOUNDS": "50",
                "SHADEMAP_TERRAIN_MAX_DISTINCT_TILES": "9999",
            },
            clear=False,
        ):
            client = self._new_client()
            _, csrf = self._login_session("shade_metrics_admin", "adminpass123", client=client)
            headers = self._session_headers(csrf)

            with patch(
                "gardenops.routers.shademap._fetch_overpass_features",
                return_value=[],
            ):
                features_response = client.get(
                    "/api/shademap/features",
                    headers=headers,
                    params=_FEATURE_BOUNDS,
                )
            self.assertEqual(features_response.status_code, 200)

            with (
                patch("gardenops.routers.shademap._perform_sdk_validation"),
                patch(
                    "gardenops.routers.shademap.local_terrain_available",
                    return_value=False,
                ),
            ):
                config = client.get("/api/shademap/config", headers=headers)
            self.assertEqual(config.status_code, 200)
            token = parse_qs(urlparse(config.json()["terrain_url_template"]).query)["token"][0]

            with (
                patch(
                    "gardenops.routers.shademap._request_bytes",
                    return_value=(b"png-bytes", "image/png"),
                ),
                patch(
                    "gardenops.routers.shademap.sample_local_terrain_tile",
                    return_value=None,
                ),
                patch(
                    "gardenops.routers.shademap.local_terrain_signature",
                    return_value=None,
                ),
                patch(
                    "gardenops.routers.shademap._house_overlaps_tile",
                    return_value=False,
                ),
            ):
                terrain_response = client.get(
                    f"/shademap/terrain/1/0/0.png?token={token}",
                    headers=headers,
                )
            self.assertEqual(terrain_response.status_code, 200)

            metrics = client.get("/api/auth/security-metrics", headers=headers)
            alerts = client.get("/api/auth/security-alerts", headers=headers)

        self.assertEqual(metrics.status_code, 200)
        metrics_payload = metrics.json()
        self.assertEqual(metrics_payload["rates"]["shademap_features_cache_misses_per_5m"], 1)
        self.assertEqual(metrics_payload["rates"]["shademap_features_cache_miss_ratio_pct_5m"], 100)
        self.assertEqual(metrics_payload["rates"]["shademap_terrain_remote_misses_per_5m"], 1)
        self.assertEqual(metrics_payload["rates"]["shademap_terrain_remote_miss_ratio_pct_5m"], 100)

        self.assertEqual(alerts.status_code, 200)
        alert_names = {alert["name"] for alert in alerts.json().get("alerts", [])}
        self.assertIn("shademap_features_cache_miss_spike_5m", alert_names)
        self.assertIn("shademap_terrain_remote_miss_spike_5m", alert_names)

    def test_plot_elevations_shape(self) -> None:
        response = self.client.get("/api/plots/elevations")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("available", body)
        self.assertIn("elevations", body)
        self.assertIn("overrides", body)
        self.assertIn("min_m", body)
        self.assertIn("max_m", body)
        self.assertIsInstance(body["elevations"], dict)
        self.assertIsInstance(body["overrides"], dict)

    def test_patch_elevation_overrides(self) -> None:
        default_garden_id = self._get_default_garden_id()
        headers = {"x-garden-id": str(default_garden_id)}
        conn = db.get_db()
        try:
            conn.execute(
                "INSERT INTO plots"
                " (plot_id, garden_id, zone_code, zone_name, plot_number,"
                "  grid_row, grid_col)"
                " VALUES ('EL1', %s, 'E', 'E', 1, 29, 21) ON CONFLICT DO NOTHING",
                (default_garden_id,),
            )
            conn.execute(
                "INSERT INTO plot_elevations"
                " (plot_id, elevation_m, cache_sig, garden_id)"
                " VALUES ('EL1', 42.50, 'testsig', %s) ON CONFLICT DO NOTHING",
                (default_garden_id,),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        with (
            patch("gardenops.routers.shademap.local_terrain_signature", return_value="testsig"),
            patch("gardenops.routers.shademap._elevation_cache_sig", return_value="testsig"),
            patch(
                "gardenops.routers.shademap._compute_and_cache_elevations",
                return_value={"EL1": 42.5},
            ),
        ):
            response = self.client.patch(
                "/api/plots/elevations",
                headers=headers,
                json={"overrides": {"EL1": 41.0}},
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["elevations"]["EL1"], 41.0)
            self.assertEqual(body["overrides"]["EL1"], 41.0)

            response = self.client.patch(
                "/api/plots/elevations",
                headers=headers,
                json={"overrides": {"EL1": None}},
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertNotIn("EL1", body["overrides"])
            self.assertNotEqual(body["elevations"].get("EL1"), 41.0)

    def test_patch_elevation_overrides_rejects_unknown_plot_ids(self) -> None:
        response = self.client.patch(
            "/api/plots/elevations",
            json={"overrides": {"NO_SUCH_PLOT": 41.0}},
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("Unknown plot IDs", response.json()["detail"])

    def test_elevation_override_purges_terrain_cache(self) -> None:
        """PATCH /plots/elevations should purge local terrain tile cache."""
        conn = db.get_db()
        try:
            default_garden = conn.execute(
                "SELECT id FROM gardens WHERE slug = 'default' LIMIT 1",
            ).fetchone()
            assert default_garden is not None
            default_garden_id = int(default_garden["id"])
            owner = create_user(
                conn,
                username="terrain-owner",
                password=strong_password("terrain-owner-pass"),
                role="admin",
            )
            owner_user_id = int(owner["id"])
            conn.execute(
                "INSERT INTO plots"
                " (plot_id, zone_code, zone_name, plot_number,"
                "  grid_row, grid_col)"
                " VALUES ('TC1', 'T', 'T', 1, 5, 5) ON CONFLICT DO NOTHING",
            )
            conn.execute(
                """
                INSERT INTO plot_ownership (plot_id, owner_user_id, garden_id)
                VALUES ('TC1', %s, %s)
                ON CONFLICT(plot_id) DO UPDATE SET
                    owner_user_id = excluded.owner_user_id,
                    garden_id = excluded.garden_id
                """,
                (owner_user_id, default_garden_id),
            )
            conn.execute(
                "INSERT INTO plot_elevations"
                " (plot_id, elevation_m, cache_sig, garden_id)"
                " VALUES ('TC1', 50.0, 'testsig', %s) ON CONFLICT DO NOTHING",
                (default_garden_id,),
            )
            conn.execute(
                "INSERT INTO shademap_cache"
                " (garden_id, cache_kind, cache_key, fetched_at_ms,"
                "  content_type, payload_blob)"
                " VALUES (%s, 'terrain-tile', 'g:1:local:sig:15:1:1',"
                "  0, 'image/png', %s)",
                (default_garden_id, _PNG_HEADER_BYTES),
            )
            conn.execute(
                "INSERT INTO shademap_cache"
                " (garden_id, cache_kind, cache_key, fetched_at_ms,"
                "  content_type, payload_blob)"
                " VALUES (%s, 'terrain-tile', 'g:1:remote:url:15:1:1',"
                "  0, 'image/png', %s)",
                (default_garden_id, _PNG_HEADER_BYTES),
            )
            conn.commit()
        finally:
            db.return_db(conn)

        response = self.client.patch(
            "/api/plots/elevations",
            json={"overrides": {"TC1": 55.0}},
        )
        self.assertEqual(response.status_code, 200)

        conn = db.get_db()
        try:
            local_row = conn.execute(
                "SELECT 1 FROM shademap_cache"
                " WHERE garden_id = %s AND cache_kind = 'terrain-tile'"
                " AND cache_key = 'g:1:local:sig:15:1:1'",
                (default_garden_id,),
            ).fetchone()
            self.assertIsNone(local_row, "local terrain cache not purged")

            remote_row = conn.execute(
                "SELECT 1 FROM shademap_cache"
                " WHERE garden_id = %s AND cache_kind = 'terrain-tile'"
                " AND cache_key = 'g:1:remote:url:15:1:1'",
                (default_garden_id,),
            ).fetchone()
            self.assertIsNone(
                remote_row,
                "remote terrain cache not purged",
            )
        finally:
            db.return_db(conn)

    def test_expired_terrain_token_is_rejected_before_tile_fetch(self) -> None:
        garden_id = self._get_default_garden_id()
        with (
            patch.dict(
                os.environ,
                {"SHADEMAP_TILE_SIGNING_SECRET": "shade-tile-secret"},
                clear=False,
            ),
            patch("gardenops.routers.shademap.current_timestamp_ms", return_value=10_000),
        ):
            token, expires_at_ms = shademap_router._tile_token(garden_id=garden_id)

        with (
            patch.dict(
                os.environ,
                {"SHADEMAP_TILE_SIGNING_SECRET": "shade-tile-secret"},
                clear=False,
            ),
            patch(
                "gardenops.routers.shademap.current_timestamp_ms",
                return_value=expires_at_ms + 1,
            ),
            patch("gardenops.routers.shademap._request_bytes") as fetch_mock,
        ):
            response = self.client.get(
                "/shademap/terrain/1/0/0.png",
                params={"token": token},
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "ShadeMap terrain token expired")
        fetch_mock.assert_not_called()

    def test_editor_can_write_shademap_state_while_viewer_is_read_only(self) -> None:
        self._create_test_user("shade_editor", "shade-editor-pass", role="editor")
        self._create_test_user("shade_viewer", "shade-viewer-pass", role="viewer")
        auth_env = {"AUTH_REQUIRED": "true", "AUTH_MODE": "session", "AUTH_API_KEY": ""}
        with patch.dict(os.environ, auth_env, clear=False):
            editor_client, editor_headers = self._authenticated_client(
                "shade_editor",
                "shade-editor-pass",
            )
            viewer_client, viewer_headers = self._authenticated_client(
                "shade_viewer",
                "shade-viewer-pass",
            )
            saved = editor_client.patch(
                "/api/shademap/state",
                headers=editor_headers,
                json={
                    "mode": "sun-hours",
                    "selected_plot_id": None,
                    "analysis_timestamp_ms": 1_772_443_603_995,
                    "preset": "winter",
                },
            )
            viewed = viewer_client.get("/api/shademap/state", headers=viewer_headers)
            denied_state = viewer_client.patch(
                "/api/shademap/state",
                headers=viewer_headers,
                json={
                    "mode": "shadow",
                    "selected_plot_id": None,
                    "analysis_timestamp_ms": 1_772_443_603_996,
                    "preset": "summer",
                },
            )
            calibration_before = viewer_client.get(
                "/api/shademap/calibration",
                headers=viewer_headers,
            )
            obstacles_before = viewer_client.get(
                "/api/shademap/obstacles",
                headers=viewer_headers,
            )
            denied_calibration = viewer_client.patch(
                "/api/shademap/calibration",
                headers=viewer_headers,
                json={
                    "enabled": True,
                    "calibration_type": "house-corners",
                    "origin_grid_col": None,
                    "origin_grid_row": None,
                    "origin_latitude": None,
                    "origin_longitude": None,
                    "axis_grid_col": None,
                    "axis_grid_row": None,
                    "axis_latitude": None,
                    "axis_longitude": None,
                    "house_nw_latitude": 51.50110,
                    "house_nw_longitude": -0.12490,
                    "house_ne_latitude": 51.50110,
                    "house_ne_longitude": -0.12410,
                    "house_se_latitude": 51.50070,
                    "house_se_longitude": -0.12410,
                    "house_sw_latitude": 51.50070,
                    "house_sw_longitude": -0.12490,
                },
            )
            denied_obstacle_create = viewer_client.post(
                "/api/shademap/obstacles",
                headers=viewer_headers,
                json={
                    "label": "Viewer must not add this",
                    "kind": "tree",
                    "linked_plot_id": None,
                    "latitude": 51.50090,
                    "longitude": -0.12440,
                    "height_m": 4.8,
                    "crown_radius_m": 2.4,
                    "active": True,
                },
            )
            denied_obstacle_delete = viewer_client.delete(
                "/api/shademap/obstacles/999999",
                headers=viewer_headers,
            )
            state_after = viewer_client.get("/api/shademap/state", headers=viewer_headers)
            calibration_after = viewer_client.get(
                "/api/shademap/calibration",
                headers=viewer_headers,
            )
            obstacles_after = viewer_client.get(
                "/api/shademap/obstacles",
                headers=viewer_headers,
            )

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(viewed.status_code, 200)
        self.assertEqual(viewed.json()["mode"], "sun-hours")
        for denied in (
            denied_state,
            denied_calibration,
            denied_obstacle_create,
            denied_obstacle_delete,
        ):
            self.assertEqual(denied.status_code, 403)
        self.assertEqual(state_after.json(), viewed.json())
        self.assertEqual(calibration_after.json(), calibration_before.json())
        self.assertEqual(obstacles_after.json(), obstacles_before.json())

    def test_shade_panel_exposes_deterministic_mode_and_render_proof_hooks(self) -> None:
        source = (
            Path(__file__).parents[1] / "frontend" / "src" / "components" / "shadePanel.ts"
        ).read_text(encoding="utf-8")

        self.assertIn('select.id = "shade-mode-select"', source)
        self.assertIn("mode: this.activeMode", source)
        self.assertIn('setSunExposure(this.activeMode === "sun-hours")', source)
        self.assertIn('root.dataset["renderRevision"]', source)
        self.assertIn('root.dataset["canvasWidth"]', source)
        self.assertIn('"gardenops:shade-render-state"', source)
        self.assertIn("contextEpoch !== this.gardenContextEpoch", source)
        self.assertIn('root.dataset["writeAccess"]', source)
        self.assertIn("DEFAULT_BASEMAP_TILE_URL", source)
        self.assertIn("VITE_SHADEMAP_BASEMAP_URL", source)
        self.assertIn("loadShadeMapRuntime", source)
        self.assertIn("runtime_script_url", source)
        self.assertIn('"/shademap/runtime.js"', source)
        self.assertIn("trustedShadeMapRuntimeScriptUrl", source)
        self.assertIn('createPolicy("gardenops-html"', source)

    def test_shade_token_retry_and_mobile_sheet_recovery_contracts(self) -> None:
        root = Path(__file__).parents[1]
        panel_source = (root / "frontend" / "src" / "components" / "shadePanel.ts").read_text(
            encoding="utf-8",
        )
        app_source = (root / "frontend" / "src" / "app.ts").read_text(encoding="utf-8")

        self.assertIn("initial.status !== 401 && initial.status !== 403", panel_source)
        self.assertIn("const refreshedConfig = await this.refreshTerrainConfig()", panel_source)
        self.assertIn("const retry = await this.fetchTerrainTileImage(retryUrl)", panel_source)
        self.assertIn('sheet.toggleAttribute("inert", !isOpen)', app_source)
        self.assertIn("restoreMobileMapSheetFocus()", app_source)
        self.assertIn("requestAnimationFrame(() => cameraCtrl?.fitAll())", app_source)
        self.assertIn("loadEpoch !== shadeMapPanelLoadEpoch", app_source)
