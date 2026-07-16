import io
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import laspy
import numpy as np
from pyproj import CRS, Transformer

from gardenops.services import lidar_terrain

TEST_CRS = CRS.from_epsg(32632)
TEST_X = np.array([500_000.0, 500_001.0, 500_000.0, 500_001.0])
TEST_Y = np.array([6_640_000.0, 6_640_000.0, 6_640_001.0, 6_640_001.0])
TEST_Z = np.array([10.0, 20.0, 30.0, 40.0])


def _terrain_bytes(
    *,
    compressed: bool = False,
    x: np.ndarray = TEST_X,
    y: np.ndarray = TEST_Y,
    z: np.ndarray = TEST_Z,
) -> bytes:
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.add_crs(TEST_CRS)
    terrain = laspy.LasData(header)
    terrain.x = x
    terrain.y = y
    terrain.z = z
    terrain.classification = np.full(len(x), lidar_terrain.GROUND_CLASSIFICATION)
    output = io.BytesIO()
    terrain.write(output, do_compress=compressed)
    return output.getvalue()


def _tile_for_wgs84(latitude: float, longitude: float, zoom: int) -> tuple[int, int]:
    scale = 2**zoom
    x = int((longitude + 180.0) / 360.0 * scale)
    lat_rad = math.radians(latitude)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * scale)
    return x, y


class _Header:
    def __init__(self, mins, maxs):
        self.mins = mins
        self.maxs = maxs


class _Reader:
    def __init__(self, mins, maxs):
        self.header = _Header(mins, maxs)


class TestLidarTerrainValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {
                "MEDIA_STORAGE_DIR": self.temp_dir.name,
                "SHADEMAP_LOCAL_TERRAIN_MAX_GRID_CELLS": "10000",
                "SHADEMAP_LOCAL_TERRAIN_MAX_POINTS": "1000",
                "SHADEMAP_LOCAL_TERRAIN_RESOLUTION_M": "1",
            },
            clear=False,
        )
        self.env.start()
        lidar_terrain.clear_local_terrain_cache()

    def tearDown(self) -> None:
        lidar_terrain.clear_local_terrain_cache()
        self.env.stop()
        self.temp_dir.cleanup()

    def test_generated_las_and_laz_have_known_bounds_and_elevations(self) -> None:
        to_wgs84 = Transformer.from_crs(TEST_CRS, 4326, always_xy=True)
        longitudes, latitudes = to_wgs84.transform(TEST_X, TEST_Y)

        for garden_id, suffix, compressed in ((101, ".las", False), (102, ".laz", True)):
            with self.subTest(suffix=suffix):
                status = lidar_terrain.save_uploaded_terrain(
                    garden_id,
                    _terrain_bytes(compressed=compressed),
                    f"known-terrain{suffix}",
                )
                self.assertTrue(status["available"])
                self.assertTrue(status["uploaded"])
                self.assertEqual(status["filename"], f"terrain{suffix}")

                sampled = lidar_terrain.sample_elevations_wgs84(
                    np.asarray(latitudes),
                    np.asarray(longitudes),
                    garden_id,
                )
                np.testing.assert_allclose(sampled, TEST_Z, atol=0.02)

                tile_x, tile_y = _tile_for_wgs84(
                    float(np.mean(latitudes)),
                    float(np.mean(longitudes)),
                    22,
                )
                tile = lidar_terrain.sample_local_terrain_tile(22, tile_x, tile_y, garden_id)
                self.assertIsNotNone(tile)
                assert tile is not None
                self.assertTrue(np.any(tile.coverage_mask))
                self.assertTrue(np.all(np.isfinite(tile.elevations)))

    def test_rejects_wrong_truncated_and_path_filenames_without_persisting(self) -> None:
        valid = _terrain_bytes()
        cases = (
            ("terrain.txt", valid, "must be a .las or .laz"),
            ("../terrain.las", valid, "must not contain a path"),
            ("folder\\terrain.las", valid, "must not contain a path"),
            ("terrain.las", valid[:80], "not a readable"),
        )
        for index, (filename, payload, message) in enumerate(cases, start=1):
            garden_id = 200 + index
            with self.subTest(filename=filename):
                with self.assertRaisesRegex(ValueError, message):
                    lidar_terrain.save_uploaded_terrain(garden_id, payload, filename)
                self.assertIsNone(lidar_terrain._uploaded_terrain_path(garden_id))
                terrain_dir = Path(self.temp_dir.name) / "lidar" / f"garden-{garden_id}"
                self.assertEqual(list(terrain_dir.glob(".terrain-*")), [])

    def test_rejects_invalid_garden_identifiers(self) -> None:
        for garden_id in (0, -1, "../other"):
            with self.subTest(garden_id=garden_id):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    lidar_terrain.save_uploaded_terrain(  # type: ignore[arg-type]
                        garden_id, _terrain_bytes(), "terrain.las"
                    )

    def test_grid_dimensions_reject_unsafe_bounds_and_cell_budget(self) -> None:
        invalid_bounds = (
            ((0.0, 0.0, float("nan")), (1.0, 1.0, 2.0)),
            ((1.0, 0.0, 0.0), (0.0, 1.0, 2.0)),
            ((0.0, 0.0, 3.0), (1.0, 1.0, 2.0)),
        )
        for mins, maxs in invalid_bounds:
            with self.subTest(mins=mins, maxs=maxs):
                with self.assertRaisesRegex(ValueError, "bounds"):
                    lidar_terrain._terrain_grid_dimensions(_Reader(mins, maxs), resolution_m=1.0)

        with patch.dict(
            os.environ,
            {"SHADEMAP_LOCAL_TERRAIN_MAX_GRID_CELLS": "100"},
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "bounds are too large"):
                lidar_terrain._terrain_grid_dimensions(
                    _Reader((0.0, 0.0, 0.0), (10_000.0, 10_000.0, 1.0)),
                    resolution_m=1.0,
                )

    def test_upload_rejects_point_and_grid_budgets(self) -> None:
        with patch.dict(
            os.environ,
            {"SHADEMAP_LOCAL_TERRAIN_MAX_POINTS": "3"},
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "point count"):
                lidar_terrain.save_uploaded_terrain(301, _terrain_bytes(), "terrain.las")

        wide = _terrain_bytes(
            x=np.array([0.0, 1_000.0]),
            y=np.array([0.0, 1_000.0]),
            z=np.array([1.0, 2.0]),
        )
        with patch.dict(
            os.environ,
            {"SHADEMAP_LOCAL_TERRAIN_MAX_GRID_CELLS": "100"},
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "bounds are too large"):
                lidar_terrain.save_uploaded_terrain(302, wide, "terrain.las")

    def test_processing_failure_leaves_previous_terrain_active(self) -> None:
        original = _terrain_bytes()
        lidar_terrain.save_uploaded_terrain(401, original, "terrain.las")
        active = lidar_terrain._uploaded_terrain_path(401)
        assert active is not None

        with patch.object(
            lidar_terrain,
            "_build_dataset",
            side_effect=RuntimeError("derived grid failed"),
        ):
            with self.assertRaisesRegex(ValueError, "not a readable"):
                lidar_terrain.save_uploaded_terrain(
                    401, _terrain_bytes(compressed=True), "replacement.laz"
                )

        self.assertEqual(lidar_terrain._uploaded_terrain_path(401), active)
        self.assertEqual(active.read_bytes(), original)
        self.assertEqual(list(active.parent.glob(".terrain-*")), [])

    def test_activation_persists_the_prepared_grid_cache(self) -> None:
        saved: list[tuple[str, lidar_terrain.LocalTerrainDataset]] = []
        with patch.object(
            lidar_terrain,
            "_grid_cache_save",
            lambda signature, dataset: saved.append((signature, dataset)),
        ):
            lidar_terrain.save_uploaded_terrain(450, _terrain_bytes(), "terrain.las")

        self.assertEqual(len(saved), 1)
        signature, dataset = saved[0]
        self.assertEqual(signature, lidar_terrain.local_terrain_signature(450))
        self.assertEqual(dataset.signature, signature)
        self.assertEqual(dataset.path, lidar_terrain._uploaded_terrain_path(450))
        np.testing.assert_allclose(dataset.grid, np.array([[30.0, 40.0], [10.0, 20.0]]))

    def test_activation_rollback_restores_old_file_and_cache(self) -> None:
        original = _terrain_bytes()
        replacement = _terrain_bytes(z=np.array([50.0, 60.0, 70.0, 80.0]))
        lidar_terrain.save_uploaded_terrain(501, original, "terrain.las")
        old_signature = lidar_terrain.local_terrain_signature(501)
        self.assertIsNotNone(lidar_terrain._dataset(501))

        prepared = lidar_terrain.prepare_uploaded_terrain(501, replacement, "replacement.laz")
        prepared.activate()
        self.assertEqual(
            lidar_terrain._uploaded_terrain_path(501).suffix,  # type: ignore[union-attr]
            ".laz",
        )
        prepared.rollback()

        restored = lidar_terrain._uploaded_terrain_path(501)
        assert restored is not None
        self.assertEqual(restored.suffix, ".las")
        self.assertEqual(restored.read_bytes(), original)
        self.assertEqual(lidar_terrain.local_terrain_signature(501), old_signature)
        self.assertIsNotNone(lidar_terrain._dataset(501))

    def test_replace_finalize_and_remove_clean_files_and_cache(self) -> None:
        lidar_terrain.save_uploaded_terrain(601, _terrain_bytes(), "terrain.las")
        old_signature = lidar_terrain.local_terrain_signature(601)
        assert old_signature is not None
        self.assertIsNotNone(lidar_terrain._dataset(601))

        replacement = _terrain_bytes(
            compressed=True,
            z=np.array([100.0, 110.0, 120.0, 130.0]),
        )
        lidar_terrain.save_uploaded_terrain(601, replacement, "terrain.laz")
        terrain_dir = Path(self.temp_dir.name) / "lidar" / "garden-601"
        self.assertFalse((terrain_dir / "terrain.las").exists())
        self.assertTrue((terrain_dir / "terrain.laz").exists())
        self.assertEqual(list(terrain_dir.glob("*.backup")), [])
        self.assertNotIn(old_signature, lidar_terrain._DATASET_CACHE)

        status = lidar_terrain.clear_uploaded_terrain(601)
        self.assertFalse(status["uploaded"])
        self.assertEqual(list(terrain_dir.glob("terrain.*")), [])
        self.assertEqual(lidar_terrain._DATASET_CACHE, {})

    def test_finalize_keeps_a_committed_replacement_when_backup_cleanup_fails(self) -> None:
        original = _terrain_bytes()
        replacement = _terrain_bytes(compressed=True, z=np.array([50.0, 60.0, 70.0, 80.0]))
        lidar_terrain.save_uploaded_terrain(602, original, "terrain.las")
        prepared = lidar_terrain.prepare_uploaded_terrain(602, replacement, "replacement.laz")
        prepared.activate()
        backup = next(iter(prepared.backups.values()))
        original_unlink = Path.unlink

        def fail_only_backup(path: Path, *args, **kwargs):
            if path == backup:
                raise PermissionError("backup cleanup denied")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", fail_only_backup):
            failed = prepared.finalize()

        active = lidar_terrain._uploaded_terrain_path(602)
        assert active is not None
        self.assertEqual(active.suffix, ".laz")
        self.assertEqual(active.read_bytes(), replacement)
        self.assertEqual(failed, (backup,))
        self.assertTrue(backup.exists())
