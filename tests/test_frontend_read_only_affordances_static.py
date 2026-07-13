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


def test_read_only_role_indicator_is_visible_in_desktop_and_mobile_shells() -> None:
    app = _read("frontend/src/app.ts")
    layout = _read("frontend/src/components/layout.ts")
    styles = _read("frontend/src/style.css")

    assert layout.count("data-garden-role hidden") == 2
    assert "roleChip.hidden = me.write_access;" in app
    role_rule = styles.split(".garden-role-chip {", 1)[1].split("}", 1)[0]
    assert "display: inline-flex;" in role_rule
    assert styles.count(".garden-role-chip {") == 1
