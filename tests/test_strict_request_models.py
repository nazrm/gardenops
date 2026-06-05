"""Regression tests for strict request-body schemas."""

from __future__ import annotations

import unittest
from typing import Any

from pydantic import ValidationError

from gardenops.models import ImportBody, LayoutExportBody, ShadeMapObstacleBody, SnapshotBody
from gardenops.routers.auth import AdminCreateUserBody
from gardenops.routers.calendar import CreateCalendarSubscriptionBody
from gardenops.routers.media import CreateMediaLinkBody


class TestStrictRequestModels(unittest.TestCase):
    def assert_rejects_extra(self, model: type, payload: dict[str, Any]) -> None:
        with self.assertRaises(ValidationError) as ctx:
            model.model_validate({**payload, "unexpected_admin": True})
        self.assertIn("Extra inputs are not permitted", str(ctx.exception))

    def test_admin_create_user_rejects_extra_fields(self) -> None:
        self.assert_rejects_extra(
            AdminCreateUserBody,
            {
                "username": "strict-user",
                "password": "VeryStrongPass!123",
                "role": "viewer",
            },
        )

    def test_calendar_subscription_rejects_extra_fields(self) -> None:
        self.assert_rejects_extra(
            CreateCalendarSubscriptionBody,
            {"preset_key": "essential"},
        )

    def test_media_link_rejects_extra_fields(self) -> None:
        self.assert_rejects_extra(
            CreateMediaLinkBody,
            {"target_type": "plant", "target_id": "P1"},
        )

    def test_shared_snapshot_body_rejects_extra_fields(self) -> None:
        self.assert_rejects_extra(SnapshotBody, {"name": "safe snapshot"})

    def test_layout_import_rejects_extra_fields(self) -> None:
        self.assert_rejects_extra(
            ImportBody,
            {
                "plots": [
                    {
                        "plot_id": "A1",
                        "zone_code": "A",
                        "zone_name": "Alpha",
                        "plot_number": 1,
                        "grid_row": 1,
                        "grid_col": 1,
                    },
                ],
            },
        )

    def test_direct_shademap_obstacle_rejects_export_id(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            ShadeMapObstacleBody.model_validate(
                {
                    "id": 42,
                    "label": "Cherry tree",
                    "kind": "tree",
                    "latitude": 60.38,
                    "longitude": 5.27,
                    "height_m": 4.9,
                    "crown_radius_m": 1.9,
                    "active": True,
                },
            )
        self.assertIn("Extra inputs are not permitted", str(ctx.exception))

    def test_direct_shademap_obstacle_rejects_other_extra_fields(self) -> None:
        self.assert_rejects_extra(
            ShadeMapObstacleBody,
            {
                "label": "Cherry tree",
                "kind": "tree",
                "latitude": 60.38,
                "longitude": 5.27,
                "height_m": 4.9,
                "crown_radius_m": 1.9,
                "active": True,
            },
        )

    def test_layout_import_accepts_exported_obstacle_id(self) -> None:
        body = ImportBody.model_validate(
            {
                "plots": [
                    {
                        "plot_id": "A1",
                        "zone_code": "A",
                        "zone_name": "Alpha",
                        "plot_number": 1,
                        "grid_row": 1,
                        "grid_col": 1,
                    },
                ],
                "shademap_obstacles": [
                    {
                        "id": 42,
                        "label": "Cherry tree",
                        "kind": "tree",
                        "latitude": 60.38,
                        "longitude": 5.27,
                        "height_m": 4.9,
                        "crown_radius_m": 1.9,
                        "active": True,
                    },
                ],
            },
        )
        self.assertEqual(body.shademap_obstacles[0].id, 42)

    def test_onboarding_layout_import_reuses_plot_count_limit(self) -> None:
        plot = {
            "plot_id": "A1",
            "zone_code": "A",
            "zone_name": "Alpha",
            "plot_number": 1,
            "grid_row": 1,
            "grid_col": 1,
        }
        with self.assertRaises(ValidationError):
            LayoutExportBody.model_validate(
                {"plots": [plot | {"plot_id": f"A{i}"} for i in range(1001)]}
            )

    def test_onboarding_layout_import_reuses_obstacle_count_limit(self) -> None:
        plot = {
            "plot_id": "A1",
            "zone_code": "A",
            "zone_name": "Alpha",
            "plot_number": 1,
            "grid_row": 1,
            "grid_col": 1,
        }
        obstacle = {
            "label": "Cherry tree",
            "kind": "tree",
            "latitude": 60.38,
            "longitude": 5.27,
            "height_m": 4.9,
            "crown_radius_m": 1.9,
            "active": True,
        }
        with self.assertRaises(ValidationError):
            LayoutExportBody.model_validate(
                {
                    "plots": [plot],
                    "shademap_obstacles": [
                        obstacle | {"label": f"Obstacle {i}"} for i in range(501)
                    ],
                },
            )
