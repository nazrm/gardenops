from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_nested_unit_editor_is_callback_driven_and_has_all_editable_fields() -> None:
    panel = _read("frontend/src/components/mapObjects.ts")

    assert "onUpdateUnit?:" in panel
    assert "patch: Partial<MapObjectUnitInput>" in panel
    assert "function buildUnitEditor(" in panel
    assert "makeUnitTypeSelect(unit.unit_type, !canEditUnit)" in panel
    assert "makeShapeSelect(unit.shape_type, !canEditUnit)" in panel
    assert "makeColorInput(unit.style.color, !canEditUnit)" in panel
    assert 'makeField(t("map.object_width"), widthInput)' in panel
    assert 'makeField(t("map.object_height"), heightInput)' in panel
    assert "clampUnitGeometry(" in panel
    assert "onUpdateUnit(object.public_id, unit.public_id, {" in panel
    assert "updateMapObjectUnitApi" not in panel
    assert "fetch(" not in panel


def test_nested_unit_tiles_select_instead_of_deleting_and_viewers_can_inspect() -> None:
    panel = _read("frontend/src/components/mapObjects.ts")
    grid = panel.split("function buildUnitGrid(", 1)[1].split("function buildUnitEditor(", 1)[0]

    assert "selectNestedUnit(object.public_id, unit.public_id, params);" in grid
    assert "params.onDeleteUnit" not in grid
    assert "cell.disabled" not in grid
    assert 'cell.setAttribute("aria-pressed"' in grid
    assert "buildUnitEditor(selected, selectedUnit, params)" in panel
    assert "const canEditUnit = params.canWrite && onUpdateUnit !== undefined;" in panel
    assert "deleteUnit.disabled = !params.canWrite;" in panel
    assert 'deleteUnit.addEventListener("click"' in panel


def test_nested_unit_update_api_uses_the_existing_patch_endpoint() -> None:
    api = _read("frontend/src/services/api.ts")

    assert "export async function updateMapObjectUnitApi(" in api
    assert "unit: Partial<MapObjectUnitInput>" in api
    assert "apiPatch<MapObjectUnit>(" in api
    assert "/map-objects/${objectPublicId}/units/${unitPublicId}" in api


def test_nested_unit_editor_is_wired_to_the_guarded_refresh_flow() -> None:
    app = _read("frontend/src/app.ts")
    update = app.split("async function updateNestedMapUnit(", 1)[1].split(
        "async function deleteNestedMapUnit(", 1
    )[0]

    assert "onUpdateUnit: (objectPublicId, unitPublicId, patch)" in app
    assert "void updateNestedMapUnit(objectPublicId, unitPublicId, patch);" in app
    assert "if (!ensureWriteAccess()) return;" in update
    assert "const gardenId = getActiveGardenContext();" in update
    assert "await updateMapObjectUnitApi(" in update
    assert "state.selectedMapObjectId = objectPublicId;" in update
    assert "await fetchMapObjects();" in update
    assert 'showToast(t("map.unit_updated"), "success");' in update
    assert "showFetchError(err);" in update
