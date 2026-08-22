from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_normal_plot_selectors_keep_ids_as_values_and_use_human_labels() -> None:
    journal = _read("frontend/src/components/journal.ts")
    issues = _read("frontend/src/components/issues.ts")
    harvest = _read("frontend/src/components/harvest.ts")

    assert "formatPlotLabel(plot.plot_id, plot.zone_name, null, plot.display_name)" in journal
    assert "opt.value = plot.plot_id;" in journal
    assert "opt.textContent = plotChoiceLabel(plot);" in journal
    assert "getKey: (plot) => plot.plot_id" in issues
    assert "getLabel: (plot) => plotChoiceLabel(plot)" in issues
    assert "getKey: (plot) => plot.plot_id" in harvest
    assert "getLabel: (plot) => plotChoiceLabel(plot)" in harvest


def test_calendar_plot_controls_use_the_same_human_label_formatter() -> None:
    calendar = _read("frontend/src/tabs/calendarTab.ts")

    assert (
        calendar.count(
            "formatPlotLabel(plot.plot_id, plot.zone_name, null, plot.display_name)",
        )
        >= 2
    )
    assert "getKey: (plot) => plot.plot_id" in calendar
    assert "linkedPlot?.display_name ?? plot?.display_name" in calendar
