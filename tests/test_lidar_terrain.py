import os
import unittest
from unittest.mock import patch

from gardenops.services import lidar_terrain


class _Header:
    def __init__(self, mins, maxs):
        self.mins = mins
        self.maxs = maxs


class _Reader:
    def __init__(self, mins, maxs):
        self.header = _Header(mins, maxs)


class TestLidarTerrainValidation(unittest.TestCase):
    def test_grid_dimensions_reject_unbounded_header_extents(self) -> None:
        old_limit = os.environ.get("SHADEMAP_LOCAL_TERRAIN_MAX_GRID_CELLS")
        os.environ["SHADEMAP_LOCAL_TERRAIN_MAX_GRID_CELLS"] = "100"
        try:
            with self.assertRaisesRegex(ValueError, "bounds are too large"):
                lidar_terrain._terrain_grid_dimensions(
                    _Reader((0.0, 0.0, 0.0), (10_000.0, 10_000.0, 0.0)),
                    resolution_m=1.0,
                )
        finally:
            if old_limit is None:
                os.environ.pop("SHADEMAP_LOCAL_TERRAIN_MAX_GRID_CELLS", None)
            else:
                os.environ["SHADEMAP_LOCAL_TERRAIN_MAX_GRID_CELLS"] = old_limit

    def test_uploaded_terrain_is_validated_before_persisting(self) -> None:
        with patch(
            "gardenops.services.lidar_terrain._validate_uploaded_terrain_payload",
            side_effect=ValueError("LiDAR upload is not a readable LAS/LAZ file"),
        ):
            with self.assertRaisesRegex(ValueError, "not a readable"):
                lidar_terrain.save_uploaded_terrain(999_001, b"not-las", "terrain.las")

        self.assertIsNone(lidar_terrain._uploaded_terrain_path(999_001))

    def test_point_count_limit_rejects_large_upload(self) -> None:
        with patch.dict(
            os.environ,
            {"SHADEMAP_LOCAL_TERRAIN_MAX_POINTS": "10"},
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "point count"):
                lidar_terrain._enforce_point_count_limit(11)
