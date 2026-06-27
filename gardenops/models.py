"""Pydantic request/response models shared across routers."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from gardenops.public_ids import normalize_public_id


class StrictBaseModel(BaseModel):
    """Reject unknown request fields instead of silently ignoring probes or drift."""

    model_config = ConfigDict(extra="forbid")


class SnapshotBody(StrictBaseModel):
    name: str = Field(min_length=1, max_length=120)


class HouseState(StrictBaseModel):
    row: int = Field(ge=1, le=100)
    col: int = Field(ge=1, le=100)
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class LayoutStateBody(HouseState):
    north_degrees: int = Field(ge=0, le=359)
    grid_rows: int = Field(default=30, ge=5, le=100)
    grid_cols: int = Field(default=22, ge=5, le=100)


ShadeMapMode = Literal["shadow", "sun-hours"]
ShadeMapPreset = Literal["now", "custom", "spring", "summer", "autumn", "winter"]


class ShadeMapStateBody(StrictBaseModel):
    mode: ShadeMapMode = "shadow"
    selected_plot_id: str | None = None
    analysis_timestamp_ms: int = Field(ge=0)
    preset: ShadeMapPreset = "now"


ShadeMapObstacleKind = Literal["tree", "structure"]


class ShadeMapCalibrationBody(StrictBaseModel):
    enabled: bool = False
    calibration_type: Literal["two-point", "house-corners"] = "house-corners"
    origin_grid_col: float | None = None
    origin_grid_row: float | None = None
    origin_latitude: float | None = None
    origin_longitude: float | None = None
    axis_grid_col: float | None = None
    axis_grid_row: float | None = None
    axis_latitude: float | None = None
    axis_longitude: float | None = None
    house_nw_latitude: float | None = None
    house_nw_longitude: float | None = None
    house_ne_latitude: float | None = None
    house_ne_longitude: float | None = None
    house_se_latitude: float | None = None
    house_se_longitude: float | None = None
    house_sw_latitude: float | None = None
    house_sw_longitude: float | None = None


class ShadeMapObstacleBody(StrictBaseModel):
    label: str = Field(min_length=1, max_length=120)
    kind: ShadeMapObstacleKind = "tree"
    linked_plot_id: str | None = None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    height_m: float = Field(gt=0)
    crown_radius_m: float = Field(gt=0)
    active: bool = True


class ShadeMapObstacleImportItem(ShadeMapObstacleBody):
    id: int | None = Field(default=None, ge=1)


class ImportedLayoutStateBody(HouseState):
    north_degrees: int | None = Field(default=None, ge=0, le=359)
    direction: str | None = Field(default=None, pattern="^(north|east|south|west)$")
    grid_rows: int | None = Field(default=None, ge=5, le=100)
    grid_cols: int | None = Field(default=None, ge=5, le=100)


class PlotImportItem(StrictBaseModel):
    plot_id: str = Field(min_length=1, max_length=40)
    zone_code: str = Field(min_length=1, max_length=20)
    zone_name: str = Field(min_length=1, max_length=120)
    plot_number: int
    grid_row: int = Field(ge=1, le=100)
    grid_col: int = Field(ge=1, le=100)
    sub_zone: str | None = Field(default="", max_length=120)
    notes: str | None = Field(default="", max_length=4000)
    color: str | None = None

    @field_validator("plot_id")
    @classmethod
    def validate_plot_id(cls, value: str) -> str:
        return normalize_public_id(value, field_name="plot_id")


class LayoutExportBody(StrictBaseModel):
    plots: list[PlotImportItem] = Field(min_length=1, max_length=1000)
    house: ImportedLayoutStateBody | None = None
    shademap: ShadeMapStateBody | None = None
    shademap_calibration: ShadeMapCalibrationBody | None = None
    shademap_obstacles: list[ShadeMapObstacleImportItem] | None = Field(
        default=None,
        max_length=500,
    )


class ImportBody(StrictBaseModel):
    plots: list[PlotImportItem] = Field(min_length=1, max_length=1000)
    house: ImportedLayoutStateBody | None = None
    shademap: ShadeMapStateBody | None = None
    shademap_calibration: ShadeMapCalibrationBody | None = None
    shademap_obstacles: list[ShadeMapObstacleImportItem] | None = Field(
        default=None,
        max_length=500,
    )
