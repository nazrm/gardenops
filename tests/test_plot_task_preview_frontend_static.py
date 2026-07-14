from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source() -> str:
    return (ROOT / "frontend/src/components/plotInteractions.ts").read_text(encoding="utf-8")


def test_plot_task_preview_caches_live_data_and_names_offline_states() -> None:
    source = _source()

    assert "function getCachedPlotTaskPreview(" in source
    assert source.count("cacheTaskList(") >= 2
    assert "if (!isOnline()) {" in source
    assert 'dataState: "cached"' in source
    assert 'dataState: "unavailable"' in source
    assert '"tasks.offline_cached"' in source
    assert '"tasks.offline_unavailable"' in source


def test_active_plot_task_panel_refreshes_for_connectivity_and_queue_changes() -> None:
    source = _source()

    assert "plotTaskPanelListenersBound" in source
    assert "onConnectivityChange(() => {" in source
    assert "onOfflineQueueChange(() => {" in source
    assert source.count("void refreshActivePlotTasksPreview();") == 2
    assert "active.state.selectedPlotId !== active.plotId" in source
    assert "activatePlotTasksPanel(state, plotId, cbs);" in source
    assert "deactivatePlotTasksPanel();" in source


def test_plot_task_dialogs_keep_panel_parent_and_restore_focus_after_refresh() -> None:
    source = _source()

    assert '".drawer, .bottom-sheet"' in source
    assert "openTaskCompletionDialog(task, plantNames" in source
    assert "}, { modalParent });" in source
    assert source.count("modalParent,") >= 2
    assert "function restorePlotTaskPreviewFocus(" in source
    assert 'section.querySelector<HTMLElement>(".drawer-section-header")' in source
    assert source.count("focusTaskId: task.id") == 2
