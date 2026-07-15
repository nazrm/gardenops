from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_mobile_plot_sheet_exposes_labeled_edit_and_delete_buttons_for_writers() -> None:
    sheet = _read("frontend/src/components/bottomSheet.ts")

    assert "onEditPlot?: (() => void) | undefined;" in sheet
    assert "if (params.canWrite !== false && params.onEditPlot) {" in sheet
    assert 'editPlotBtn.className = "drawer-edit-plot-btn";' in sheet
    assert 'editPlotBtn.dataset["editPlot"] = plotId;' in sheet
    assert 'editPlotBtn.textContent = t("common.edit");' in sheet
    assert 'editPlotBtn.setAttribute("aria-label", t("popover.edit_plot"));' in sheet
    assert "dismissBottomSheet(true);\n      onClose();" in sheet
    assert "window.requestAnimationFrame(() => params.onEditPlot?.());" in sheet
    assert "if (params.canWrite !== false && params.onDeletePlot) {" in sheet
    assert 'deletePlotBtn.dataset["deletePlot"] = plotId;' in sheet
    assert 'deletePlotBtn.textContent = t("common.delete");' in sheet
    assert 'deletePlotBtn.setAttribute("aria-label", t("popover.delete_plot"));' in sheet


def test_mobile_edit_mode_selection_opens_the_write_gated_plot_sheet() -> None:
    app = _read("frontend/src/app.ts")
    interactions = _read("frontend/src/components/plotInteractions.ts")

    assert (
        "if (isMobile()) {\n            void selectPlot(state, plot.plot_id, plotCbs);\n          }"
        in app
    )
    mobile_selection = interactions.split("if (cbs.isMobile()) {", 1)[1].split(
        "void hydrateActivePlotPanel", 1
    )[0]
    assert "...(cbs.canWrite()" in mobile_selection
    assert "onEditPlot: () => cbs.onEditPlot(plotId)," in mobile_selection
    assert "onDeletePlot: () => void cbs.deletePlot(plotId)," in mobile_selection


def test_mobile_plot_note_matches_the_available_touch_actions() -> None:
    i18n = _read("frontend/src/core/i18n.ts")
    english_note = (
        "Tap a plot to open its details. "
        "Editors can edit or delete it; multi-select still requires desktop."
    )
    norwegian_note = (
        "Trykk p\u00e5 et felt for \u00e5 \u00e5pne detaljene. "
        "Redakt\u00f8rer kan redigere eller slette det; flervalg krever fortsatt desktop."
    )

    assert f'"map.mobile_note": "{english_note}"' in i18n
    assert f'"map.mobile_note": "{norwegian_note}"' in i18n


def test_phone_plot_tiles_do_not_compete_with_inline_extend_touch_target() -> None:
    styles = _read("frontend/src/style.css")

    assert "Plot cells are too small to share a touch target with the extend action." in styles
    assert ".plot-extend-btn {\n    display: none;\n  }" in styles
