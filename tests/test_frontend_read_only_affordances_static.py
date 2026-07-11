from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_plants_table_omits_row_write_controls_when_read_only() -> None:
    source = _read("frontend/src/components/dataTables.ts")

    assert "canWrite: boolean;" in source
    assert "if (canWrite && onToggleSelect)" in source
    assert source.count("if (canWrite) {") >= 3
    assert "const totalCols = columns.length + (canWrite ? 1 : 0)" in source


def test_app_passes_active_garden_write_access_and_clears_stale_selection() -> None:
    source = _read("frontend/src/app.ts")

    assert "canWrite: canWriteInGarden," in source
    assert "canWriteInGarden ? () => toggleSelectAllPlants() : undefined" in source
    assert "if (!canWriteInGarden) return;" in source
    assert "!canWriteInGarden || selectedPlantIds.size === 0" in source
    assert '"mobile-fab",' in source
    assert source.count("renderIndoorPlants(container, { canWrite: canWriteInGarden })") == 3
    assert "renderIndoorPlants(content, { canWrite: canWriteInGarden })" in source
