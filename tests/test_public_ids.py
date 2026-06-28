import pytest
from pydantic import ValidationError

from gardenops.models import PlotImportItem
from gardenops.routers.plants import (
    BatchJournalEntryBody,
    BatchUpdateBody,
    CreatePlantBody,
    _parse_plot_assignments,
)
from gardenops.routers.plots import BatchMoveItem, CreatePlotBody, SeenGrowingUpdate, UpdatePlotBody


@pytest.mark.parametrize(
    "unsafe_id",
    [
        "../admin",
        "..%2Fadmin",
        "plot/child",
        "plot\\child",
        "plot?x=1",
        "plot#frag",
        "plot\x00id",
    ],
)
def test_plot_public_id_entry_points_reject_path_unsafe_values(unsafe_id: str) -> None:
    with pytest.raises(ValidationError):
        CreatePlotBody(
            plot_id=unsafe_id,
            zone_code="A",
            zone_name="Bed",
            plot_number=1,
            grid_row=1,
            grid_col=1,
        )

    with pytest.raises(ValidationError):
        UpdatePlotBody(new_plot_id=unsafe_id)

    with pytest.raises(ValidationError):
        BatchMoveItem(plot_id=unsafe_id, grid_row=1, grid_col=1)

    with pytest.raises(ValidationError):
        SeenGrowingUpdate(plot_id=unsafe_id, plt_id="PLT-1")

    with pytest.raises(ValidationError):
        PlotImportItem(
            plot_id=unsafe_id,
            zone_code="A",
            zone_name="Bed",
            plot_number=1,
            grid_row=1,
            grid_col=1,
        )


@pytest.mark.parametrize(
    "unsafe_id",
    [
        "../PLT-1",
        "..%2FPLT-1",
        "PLT/1",
        "PLT\\1",
        "PLT?x=1",
        "PLT#frag",
        "PLT\x00id",
    ],
)
def test_plant_public_id_entry_points_reject_path_unsafe_values(unsafe_id: str) -> None:
    with pytest.raises(ValidationError):
        CreatePlantBody(plt_id=unsafe_id, name="Rose")

    with pytest.raises(ValidationError):
        BatchUpdateBody(plt_ids=[unsafe_id])

    with pytest.raises(ValidationError):
        BatchJournalEntryBody(
            plt_ids=[unsafe_id],
            event_type="planted",
            occurred_on="2026-06-27",
        )

    with pytest.raises(ValueError):
        _parse_plot_assignments(f"{unsafe_id}=1")


def test_public_ids_allow_current_ascii_id_style() -> None:
    plot = CreatePlotBody(
        plot_id="B1",
        zone_code="A",
        zone_name="Bed",
        plot_number=1,
        grid_row=1,
        grid_col=1,
    )
    plant = CreatePlantBody(plt_id="PLT-123_test", name="Rose")

    assert plot.plot_id == "B1"
    assert plant.plt_id == "PLT-123_test"
