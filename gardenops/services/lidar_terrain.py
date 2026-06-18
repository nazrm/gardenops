"""Local LiDAR-backed terrain sampling for ShadeMap terrain tiles."""

from __future__ import annotations

import math
import os
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Final

import laspy
import numpy as np
from PIL import Image
from pyproj import CRS, Transformer

ROOT: Final[Path] = Path(__file__).resolve().parents[2]
GROUND_CLASSIFICATION: Final[int] = 2
DEFAULT_RESOLUTION_METERS: Final[float] = 1.0
TILE_SIZE: Final[int] = 256
LOCAL_TERRAIN_SCAN_PATTERN: Final[str] = "*.laz"


@dataclass(frozen=True)
class LocalTerrainTile:
    elevations: np.ndarray
    coverage_mask: np.ndarray

    @property
    def fully_covered(self) -> bool:
        return bool(np.all(self.coverage_mask))


@dataclass(frozen=True)
class LocalTerrainDataset:
    path: Path
    signature: str
    grid: np.ndarray
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    resolution_m: float
    transformer_from_wgs84: Transformer

    @property
    def rows(self) -> int:
        return int(self.grid.shape[0])

    @property
    def cols(self) -> int:
        return int(self.grid.shape[1])


_DATASET_LOCK = threading.Lock()
_DATASET_CACHE: dict[str, LocalTerrainDataset] = {}
_ALLOWED_UPLOAD_SUFFIXES: Final[set[str]] = {".las", ".laz"}

GridCacheLoad = Callable[[str], LocalTerrainDataset | None]
GridCacheSave = Callable[[str, LocalTerrainDataset], None]

_grid_cache_load: GridCacheLoad | None = None
_grid_cache_save: GridCacheSave | None = None


def set_grid_cache_callbacks(
    load_fn: GridCacheLoad,
    save_fn: GridCacheSave,
) -> None:
    """Register DB-backed load/save for the parsed LiDAR grid."""
    global _grid_cache_load, _grid_cache_save
    _grid_cache_load = load_fn
    _grid_cache_save = save_fn


def serialize_dataset(ds: LocalTerrainDataset) -> dict:
    """Convert a LocalTerrainDataset to a dict suitable for DB storage."""
    target_crs = ds.transformer_from_wgs84.target_crs
    assert target_crs is not None
    return {
        "grid_blob": ds.grid.astype(np.float32).tobytes(),
        "grid_rows": ds.rows,
        "grid_cols": ds.cols,
        "min_x": ds.min_x,
        "max_x": ds.max_x,
        "min_y": ds.min_y,
        "max_y": ds.max_y,
        "resolution_m": ds.resolution_m,
        "crs_wkt": target_crs.to_wkt(),
    }


def restore_dataset(
    data: dict,
    path: Path,
    signature: str,
) -> LocalTerrainDataset:
    """Rebuild a LocalTerrainDataset from a DB cache row."""
    grid = np.frombuffer(
        data["grid_blob"],
        dtype=np.float32,
    ).reshape(int(data["grid_rows"]), int(data["grid_cols"]))
    dataset_crs = CRS.from_wkt(data["crs_wkt"])
    return LocalTerrainDataset(
        path=path,
        signature=signature,
        grid=grid,
        min_x=float(data["min_x"]),
        max_x=float(data["max_x"]),
        min_y=float(data["min_y"]),
        max_y=float(data["max_y"]),
        resolution_m=float(data["resolution_m"]),
        transformer_from_wgs84=Transformer.from_crs(
            4326,
            dataset_crs,
            always_xy=True,
        ),
    )


def _media_storage_root() -> Path:
    raw = os.environ.get("MEDIA_STORAGE_DIR", "").strip()
    root = Path(raw) if raw else (ROOT / "media_uploads")
    if not root.is_absolute():
        root = ROOT / root
    return root


def lidar_upload_max_bytes() -> int:
    raw = os.environ.get("SHADEMAP_LOCAL_TERRAIN_MAX_UPLOAD_BYTES", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return 256 * 1024 * 1024


def _uploaded_terrain_dir(garden_id: int) -> Path:
    return _media_storage_root() / "lidar" / f"garden-{garden_id}"


def _uploaded_terrain_path(garden_id: int) -> Path | None:
    root = _uploaded_terrain_dir(garden_id)
    for suffix in (".laz", ".las"):
        candidate = root / f"terrain{suffix}"
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _terrain_source(garden_id: int | None = None) -> tuple[str, Path] | None:
    if garden_id is not None:
        uploaded = _uploaded_terrain_path(garden_id)
        if uploaded is not None:
            return f"uploaded:{garden_id}", uploaded

    explicit = os.environ.get("SHADEMAP_LOCAL_TERRAIN_PATH", "").strip()
    if explicit:
        candidate = Path(explicit).expanduser()
        if not candidate.is_absolute():
            candidate = ROOT / candidate
        return ("env", candidate) if candidate.exists() else None

    candidates = sorted(ROOT.glob(LOCAL_TERRAIN_SCAN_PATTERN))
    if len(candidates) == 1:
        return "root", candidates[0]
    return None


def _terrain_path(garden_id: int | None = None) -> Path | None:
    source = _terrain_source(garden_id)
    return source[1] if source else None


def terrain_path_for_signature(signature: str) -> Path | None:
    parts = signature.split(":", 3)
    if len(parts) < 4:
        return _terrain_path()
    source_kind = parts[0]
    if source_kind == "uploaded":
        try:
            garden_id = int(parts[1])
        except ValueError:
            return None
        return _uploaded_terrain_path(garden_id)
    return _terrain_path()


def local_terrain_signature(garden_id: int | None = None) -> str | None:
    source = _terrain_source(garden_id)
    if not source:
        return None
    source_key, path = source
    stat = path.stat()
    return f"{source_key}:{path.name}:{int(stat.st_mtime_ns)}:{stat.st_size}"


def local_terrain_available(garden_id: int | None = None) -> bool:
    return local_terrain_signature(garden_id) is not None


def local_terrain_storage_info(garden_id: int) -> dict[str, object]:
    uploaded = _uploaded_terrain_path(garden_id)
    active = _terrain_source(garden_id)
    active_path = active[1] if active else None
    uploaded_stat = uploaded.stat() if uploaded else None
    active_stat = active_path.stat() if active_path else None
    return {
        "available": active_path is not None,
        "uploaded": uploaded is not None,
        "filename": active_path.name if active_path else "",
        "uploaded_filename": uploaded.name if uploaded else "",
        "bytes": int(active_stat.st_size) if active_stat else 0,
        "uploaded_bytes": int(uploaded_stat.st_size) if uploaded_stat else 0,
        "updated_at_ms": int(active_stat.st_mtime * 1000) if active_stat else None,
        "source": active[0] if active else "none",
        "max_upload_bytes": lidar_upload_max_bytes(),
    }


def save_uploaded_terrain(
    garden_id: int, payload: bytes, original_filename: str
) -> dict[str, object]:
    suffix = Path(original_filename or "").suffix.lower()
    if suffix not in _ALLOWED_UPLOAD_SUFFIXES:
        raise ValueError("LiDAR upload must be a .las or .laz file")
    if not payload:
        raise ValueError("LiDAR upload is empty")
    max_bytes = lidar_upload_max_bytes()
    if len(payload) > max_bytes:
        raise ValueError("LiDAR upload exceeds size limit")

    target_dir = _uploaded_terrain_dir(garden_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"terrain{suffix}"
    tmp = target_dir / f".terrain-{secrets.token_hex(8)}.tmp"
    tmp.write_bytes(payload)
    tmp.replace(target)
    for other_suffix in _ALLOWED_UPLOAD_SUFFIXES - {suffix}:
        (target_dir / f"terrain{other_suffix}").unlink(missing_ok=True)
    clear_local_terrain_cache()
    return local_terrain_storage_info(garden_id)


def clear_uploaded_terrain(garden_id: int) -> dict[str, object]:
    target_dir = _uploaded_terrain_dir(garden_id)
    for suffix in _ALLOWED_UPLOAD_SUFFIXES:
        (target_dir / f"terrain{suffix}").unlink(missing_ok=True)
    clear_local_terrain_cache()
    return local_terrain_storage_info(garden_id)


def clear_local_terrain_cache() -> None:
    with _DATASET_LOCK:
        _DATASET_CACHE.clear()


def _resolution_meters() -> float:
    raw = os.environ.get("SHADEMAP_LOCAL_TERRAIN_RESOLUTION_M", "").strip()
    if not raw:
        return DEFAULT_RESOLUTION_METERS
    try:
        parsed = float(raw)
    except ValueError:
        return DEFAULT_RESOLUTION_METERS
    return parsed if parsed > 0 else DEFAULT_RESOLUTION_METERS


def _fill_nan_grid(grid: np.ndarray) -> np.ndarray:
    result = np.array(grid, copy=True, dtype=np.float32)
    global_mean = float(np.nanmean(result)) if np.isfinite(np.nanmean(result)) else 0.0

    for row_index in range(result.shape[0]):
        row = result[row_index]
        mask = np.isfinite(row)
        if not mask.any():
            continue
        if mask.all():
            continue
        row[~mask] = np.interp(
            np.flatnonzero(~mask),
            np.flatnonzero(mask),
            row[mask],
        )

    for col_index in range(result.shape[1]):
        col = result[:, col_index]
        mask = np.isfinite(col)
        if not mask.any():
            continue
        if mask.all():
            continue
        col[~mask] = np.interp(
            np.flatnonzero(~mask),
            np.flatnonzero(mask),
            col[mask],
        )

    result[~np.isfinite(result)] = global_mean
    return result


def _accumulate_average_grid(
    reader: laspy.LasReader,
    *,
    resolution_m: float,
) -> tuple[np.ndarray, float, float, float, float]:
    min_x, min_y, _ = map(float, reader.header.mins)
    max_x, max_y, _ = map(float, reader.header.maxs)
    cols = int(math.ceil((max_x - min_x) / resolution_m)) + 1
    rows = int(math.ceil((max_y - min_y) / resolution_m)) + 1

    ground_sum = np.zeros((rows, cols), dtype=np.float64)
    ground_count = np.zeros((rows, cols), dtype=np.uint32)
    all_sum = np.zeros((rows, cols), dtype=np.float64)
    all_count = np.zeros((rows, cols), dtype=np.uint32)

    for points in reader.chunk_iterator(1_000_000):
        x = np.asarray(points.x, dtype=np.float64)
        y = np.asarray(points.y, dtype=np.float64)
        z = np.asarray(points.z, dtype=np.float64)
        col = np.floor((x - min_x) / resolution_m).astype(np.int32)
        row = np.floor((max_y - y) / resolution_m).astype(np.int32)
        valid = (row >= 0) & (row < rows) & (col >= 0) & (col < cols) & np.isfinite(z)
        if not np.any(valid):
            continue

        valid_row = row[valid]
        valid_col = col[valid]
        valid_z = z[valid]
        np.add.at(all_sum, (valid_row, valid_col), valid_z)
        np.add.at(all_count, (valid_row, valid_col), 1)

        classification = np.asarray(points.classification, dtype=np.uint8)[valid]
        ground_mask = classification == GROUND_CLASSIFICATION
        if np.any(ground_mask):
            g_rows = valid_row[ground_mask]
            g_cols = valid_col[ground_mask]
            np.add.at(ground_sum, (g_rows, g_cols), valid_z[ground_mask])
            np.add.at(ground_count, (g_rows, g_cols), 1)

    use_ground = bool(np.any(ground_count > 0))
    total_sum = ground_sum if use_ground else all_sum
    total_count = ground_count if use_ground else all_count
    grid = np.divide(
        total_sum,
        total_count,
        out=np.full(total_sum.shape, np.nan, dtype=np.float64),
        where=total_count > 0,
    )
    return _fill_nan_grid(grid), min_x, max_x, min_y, max_y


def _build_dataset(path: Path, signature: str) -> LocalTerrainDataset:
    resolution_m = _resolution_meters()
    with laspy.open(path) as reader:
        crs = reader.header.parse_crs()
        if crs is None:
            raise RuntimeError(f"Local terrain file {path.name} is missing CRS metadata")
        dataset_crs = CRS.from_user_input(crs)
        grid, min_x, max_x, min_y, max_y = _accumulate_average_grid(
            reader,
            resolution_m=resolution_m,
        )
    return LocalTerrainDataset(
        path=path,
        signature=signature,
        grid=grid,
        min_x=min_x,
        max_x=max_x,
        min_y=min_y,
        max_y=max_y,
        resolution_m=resolution_m,
        transformer_from_wgs84=Transformer.from_crs(
            4326,
            dataset_crs,
            always_xy=True,
        ),
    )


def _dataset(garden_id: int | None = None) -> LocalTerrainDataset | None:
    path = _terrain_path(garden_id)
    signature = local_terrain_signature(garden_id)
    if not path or not signature:
        return None

    cached_dataset = _DATASET_CACHE.get(signature)
    if cached_dataset is not None:
        return cached_dataset

    with _DATASET_LOCK:
        cached_dataset = _DATASET_CACHE.get(signature)
        if cached_dataset is not None:
            return cached_dataset

        if _grid_cache_load is not None:
            cached = _grid_cache_load(signature)
            if cached is not None:
                _DATASET_CACHE[signature] = cached
                return cached

        dataset = _build_dataset(path, signature)
        _DATASET_CACHE[signature] = dataset

        if _grid_cache_save is not None:
            _grid_cache_save(signature, dataset)

        return dataset


def _tile_lon(x: np.ndarray, z: int) -> np.ndarray:
    return (x / (2**z)) * 360.0 - 180.0


def _tile_lat(y: np.ndarray, z: int) -> np.ndarray:
    return np.degrees(np.arctan(np.sinh(np.pi * (1.0 - (2.0 * y) / (2**z)))))


def _tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    west = _tile_lon(np.array([x], dtype=np.float64), z)[0]
    east = _tile_lon(np.array([x + 1], dtype=np.float64), z)[0]
    north = _tile_lat(np.array([y], dtype=np.float64), z)[0]
    south = _tile_lat(np.array([y + 1], dtype=np.float64), z)[0]
    return west, south, east, north


def _bbox_intersects_dataset(dataset: LocalTerrainDataset, z: int, x: int, y: int) -> bool:
    west, south, east, north = _tile_bounds(z, x, y)
    proj_x, proj_y = dataset.transformer_from_wgs84.transform(
        [west, east, east, west],
        [north, north, south, south],
    )
    tile_min_x = min(proj_x)
    tile_max_x = max(proj_x)
    tile_min_y = min(proj_y)
    tile_max_y = max(proj_y)
    return not (
        tile_max_x < dataset.min_x
        or tile_min_x > dataset.max_x
        or tile_max_y < dataset.min_y
        or tile_min_y > dataset.max_y
    )


def _sample_bilinear(
    dataset: LocalTerrainDataset,
    proj_x: np.ndarray,
    proj_y: np.ndarray,
) -> LocalTerrainTile:
    col_f = (proj_x - dataset.min_x) / dataset.resolution_m
    row_f = (dataset.max_y - proj_y) / dataset.resolution_m

    coverage_mask = (
        (col_f >= 0.0) & (col_f <= dataset.cols - 1) & (row_f >= 0.0) & (row_f <= dataset.rows - 1)
    )

    col_f = np.clip(col_f, 0.0, dataset.cols - 1)
    row_f = np.clip(row_f, 0.0, dataset.rows - 1)

    col0 = np.floor(col_f).astype(np.int32)
    row0 = np.floor(row_f).astype(np.int32)
    col1 = np.clip(col0 + 1, 0, dataset.cols - 1)
    row1 = np.clip(row0 + 1, 0, dataset.rows - 1)

    dc = col_f - col0
    dr = row_f - row0

    grid = dataset.grid
    top_left = grid[row0, col0]
    top_right = grid[row0, col1]
    bottom_left = grid[row1, col0]
    bottom_right = grid[row1, col1]

    elevations = (
        top_left * (1.0 - dc) * (1.0 - dr)
        + top_right * dc * (1.0 - dr)
        + bottom_left * (1.0 - dc) * dr
        + bottom_right * dc * dr
    ).astype(np.float32)

    return LocalTerrainTile(
        elevations=elevations,
        coverage_mask=coverage_mask,
    )


def sample_elevations_wgs84(
    latitudes: np.ndarray,
    longitudes: np.ndarray,
    garden_id: int | None = None,
) -> np.ndarray:
    """Sample elevations for WGS84 coordinates.

    Returns array of elevations in meters; NaN for out-of-coverage.
    """
    dataset = _dataset(garden_id)
    if dataset is None:
        return np.full(len(latitudes), np.nan, dtype=np.float32)

    proj_x, proj_y = dataset.transformer_from_wgs84.transform(
        longitudes,
        latitudes,
    )
    tile = _sample_bilinear(dataset, proj_x, proj_y)
    result = np.array(tile.elevations, dtype=np.float32)
    result[~tile.coverage_mask] = np.nan
    return result


def sample_local_terrain_tile(
    z: int,
    x: int,
    y: int,
    garden_id: int | None = None,
) -> LocalTerrainTile | None:
    dataset = _dataset(garden_id)
    if dataset is None:
        return None
    if not _bbox_intersects_dataset(dataset, z, x, y):
        return None

    x_coords = x + (np.arange(TILE_SIZE, dtype=np.float64) + 0.5) / TILE_SIZE
    y_coords = y + (np.arange(TILE_SIZE, dtype=np.float64) + 0.5) / TILE_SIZE
    lon = _tile_lon(x_coords, z)
    lat = _tile_lat(y_coords, z)
    lon_grid, lat_grid = np.meshgrid(lon, lat)
    proj_x, proj_y = dataset.transformer_from_wgs84.transform(lon_grid, lat_grid)
    return _sample_bilinear(dataset, proj_x, proj_y)


def decode_terrarium_png(payload: bytes) -> np.ndarray:
    with Image.open(BytesIO(payload)) as image:
        rgba = np.asarray(image.convert("RGBA"), dtype=np.float32)
    return (rgba[..., 0] * 256.0 + rgba[..., 1] + rgba[..., 2] / 256.0) - 32768.0


def encode_terrarium_png(elevations: np.ndarray) -> bytes:
    value = np.asarray(elevations, dtype=np.float32) + 32768.0
    value = np.clip(value, 0.0, 65535.996)
    red = np.floor(value / 256.0)
    green = np.floor(value - red * 256.0)
    blue = np.floor((value - np.floor(value)) * 256.0)
    rgba = np.stack(
        [
            red.astype(np.uint8),
            green.astype(np.uint8),
            blue.astype(np.uint8),
            np.full(value.shape, 255, dtype=np.uint8),
        ],
        axis=-1,
    )
    image = Image.fromarray(rgba, mode="RGBA")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
