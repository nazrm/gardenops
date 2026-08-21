from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_map_panel_uses_intent_level_area_and_container_controls() -> None:
    panel = _read("frontend/src/components/mapObjects.ts")

    assert "function buildAreaCreateForm(" in panel
    assert "function buildContainerCreateForm(" in panel
    assert 'summary.textContent = `+ ${t("map.area_add")}`;' in panel
    assert 'summary.textContent = existing' in panel
    assert 't("map.container_add_standalone")' in panel
    assert 't("map.edit_layout")' in panel
    assert "plot_kind !== \"container\"" in panel
    assert "onCreateContainer" in panel
    assert "onOpenContainer" in panel
    assert "map-container-row-main" in panel
    assert "map-object-custom-form" not in panel
    assert "fetch(" not in panel


def test_canonical_container_rows_open_existing_plot_details() -> None:
    panel = _read("frontend/src/components/mapObjects.ts")

    assert "function buildContainerRow(" in panel
    assert "container.dataset" not in panel
    assert 'open.dataset["containerPlotId"] = container.plot_id;' in panel
    assert "params.onOpenContainer(container.plot_id, open)" in panel
    assert 'container.can_archive === true' in panel
    assert "archived_at_ms" in panel


def test_container_api_uses_canonical_container_endpoints() -> None:
    api = _read("frontend/src/services/api.ts")
    app = _read("frontend/src/app.ts")

    assert "export async function createContainerApi(" in api
    assert "export async function updateContainerApi(" in api
    assert "export async function deleteContainerApi(" in api
    assert "/api/gardens/${gardenId}/containers" in api
    assert "createMapObjectUnitApi" not in app
    assert "updateMapObjectUnitApi" not in app
    assert "deleteMapObjectUnitApi" not in app


def test_container_editor_is_wired_to_guarded_refresh_flow() -> None:
    app = _read("frontend/src/app.ts")
    assert "onCreateContainer: (input) =>" in app
    assert "void createContainer(input);" in app
    assert "async function createContainer(" in app
    assert "await createContainerApi(gardenId, input);" in app
    assert "async function updateContainer(" in app
    assert "await updateContainerApi(gardenId, plotId, patch);" in app
    assert "async function deleteContainer(" in app
    assert "await deleteContainerApi(gardenId, plotId);" in app
    assert "openContainerLocation(plotId, trigger);" in app
