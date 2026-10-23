"""Retired sun/shade code stays absent without breaking existing garden data."""

import json
import os
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from gardenops.main import app, parse_layout_payload
from gardenops.models import ImportBody, LayoutExportBody
from tests.base import BaseApiTest

ROOT = Path(__file__).resolve().parents[1]
RETIRED_FIELDS = {"shademap", "shademap_calibration", "shademap_obstacles"}


def test_no_retired_routes_or_optional_dependencies() -> None:
    paths = set(app.openapi()["paths"])
    assert not any("shademap" in path or path.endswith("/lidar") for path in paths)
    assert "/api/plots/elevations" not in paths
    manifest = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "terrain" not in manifest["project"].get("optional-dependencies", {})
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    assert not {"numpy", "laspy", "lazrs", "pyproj"} & {p["name"] for p in lock["package"]}
    browser = json.loads((ROOT / "frontend/package-lock.json").read_text())
    assert not any("leaflet" in path for path in browser["packages"])


def test_legacy_layout_fields_are_ignored_but_unknown_fields_are_rejected() -> None:
    plot = {"plot_id": "A1", "zone_code": "A", "zone_name": "Alpha", "plot_number": 1}
    legacy = {
        "plots": [plot],
        "shademap": {"mode": "sun-hours"},
        "shademap_calibration": {"enabled": True},
        "shademap_obstacles": [{"label": "Old tree"}],
    }
    for model in (ImportBody, LayoutExportBody):
        parsed = model.model_validate(legacy)
        assert not RETIRED_FIELDS & parsed.model_dump().keys()
        with pytest.raises(ValidationError):
            model.model_validate({**legacy, "unexpected_field": True})
    assert parse_layout_payload(legacy) == (1, [plot], None, None)
    assert parse_layout_payload([plot]) == (1, [plot], None, None)


class TestSunShadeRemoval(BaseApiTest):
    def test_retired_api_endpoints_are_not_available(self) -> None:
        for path in (
            "/api/shademap/config",
            "/api/shademap/state",
            "/api/plots/elevations",
            f"/api/gardens/{self._get_default_garden_id()}/lidar",
        ):
            response = self.client.get(path)
            # Without built assets, the generic PATCH/DELETE plot route matches
            # "elevations" and returns 405 rather than the static fallback's 404.
            expected = (404, 405) if path == "/api/plots/elevations" else (404,)
            self.assertIn(response.status_code, expected, path)

    def test_profile_export_and_providers_no_longer_advertise_shade(self) -> None:
        profile = self.client.get("/api/auth/me")
        self.assertEqual(profile.status_code, 200)
        self.assertNotIn("shademap_available", profile.json())
        self.assertNotIn("shade_map", profile.json()["allowed_features"])
        exported = self.client.get("/api/plots/export")
        self.assertEqual(exported.status_code, 200)
        self.assertFalse(RETIRED_FIELDS & exported.json().keys())

    def test_legacy_export_import_preserves_plots_and_omits_retired_fields(self) -> None:
        exported = self.client.get("/api/plots/export").json()
        legacy = {
            **exported,
            "shademap": {"mode": "shadow"},
            "shademap_calibration": {"enabled": True},
            "shademap_obstacles": [],
        }
        with patch.dict(os.environ, {"AUTH_REQUIRED": "true", "AUTH_MODE": "session"}):
            _, csrf = self._login_session("test_admin", "testadminpass")
            headers = self._session_headers(
                csrf, extra={"x-action-reason": "legacy-layout-without-shade"}
            )
            response = self.client.post("/api/plots/import", json=legacy, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        after = self.client.get("/api/plots/export").json()
        self.assertEqual(after, exported)
