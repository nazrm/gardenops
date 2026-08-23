from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDOOR_TAB = ROOT / "frontend" / "src" / "tabs" / "indoorTab.ts"
STYLES = ROOT / "frontend" / "src" / "style.css"


def test_indoor_controls_reuse_data_view_control_treatment() -> None:
    indoor_tab = INDOOR_TAB.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    assert 'header.className = "indoor-header data-view-header filter-row";' in indoor_tab
    assert 'searchInput.className = "indoor-search";' in indoor_tab
    assert 'sortSelect.className = "indoor-sort";' in indoor_tab
    assert ".filter-row input,\n.data-view-header select {" in styles
    assert ".filter-row input:focus,\n.data-view-header select:focus {" in styles
