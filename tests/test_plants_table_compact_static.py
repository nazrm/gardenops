from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_plant_locations_are_bounded_and_have_no_inline_move_actions() -> None:
    table = _read("frontend/src/components/dataTables.ts")

    assert "const MAX_VISIBLE_PLANT_LOCATIONS = 2;" in table
    assert "ids.slice(0, MAX_VISIBLE_PLANT_LOCATIONS)" in table
    assert 'more.className = "plot-link-overflow";' in table
    assert 'className = "plot-link-action"' not in table


def test_plant_location_cell_preserves_table_layout_and_single_line_rows() -> None:
    styles = _read("frontend/src/style.css")
    location_cell = styles.split(".data-table td.plot-links-cell {", 1)[1].split("}", 1)[0]

    assert "display: flex" not in location_cell
    assert "max-width: 220px;" in location_cell
    assert "white-space: nowrap;" in location_cell
    assert ".plot-link-action" not in styles
