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


def test_viewer_and_offline_mutation_controls_are_hidden_or_disabled_by_capability() -> None:
    app = _read("frontend/src/app.ts")
    quick_actions = _read("frontend/src/components/quickActions.ts")
    task_cards = _read("frontend/src/components/tasks.ts")
    calendar = _read("frontend/src/tabs/calendarTab.ts")

    assert "mobileFab.hidden = !canWriteInGarden;" in app
    assert "if (!canWriteInGarden) closeQuickActionSheet(false);" in app
    assert "if (action.requiresWrite && options.canWrite === false) continue;" in quick_actions
    assert "const unavailableOffline = Boolean(" in quick_actions
    assert "offlineUnsupportedCompletion" in task_cards
    assert "newEventButton.hidden = !ctx.canWrite();" in calendar
    assert "newEventButton.disabled = !ctx.isOnline();" in calendar


def test_read_only_role_indicator_is_visible_in_desktop_and_mobile_shells() -> None:
    app = _read("frontend/src/app.ts")
    layout = _read("frontend/src/components/layout.ts")
    styles = _read("frontend/src/style.css")

    assert layout.count("data-garden-role hidden") == 2
    assert "roleChip.hidden = me.write_access;" in app
    role_rule = styles.split(".garden-role-chip {", 1)[1].split("}", 1)[0]
    assert "display: inline-flex;" in role_rule
    assert styles.count(".garden-role-chip {") == 1


def test_viewers_can_dismiss_personal_weather_attention_but_not_refresh_forecasts() -> None:
    weather = _read("frontend/src/components/weather.ts")
    main = _read("gardenops/main.py")

    assert "createWeatherAlertCardMarkup(alert, true)" in weather
    assert weather.count('addEventListener("click", callbacks.onCheckWeather)') == 2
    assert "if (canWriteWeather()) callbacks.onCheckWeather();" not in weather
    assert 'weather_alert_prefix = "/api/weather/alerts/"' in main
    assert "return alert_id.isdigit()" in main
