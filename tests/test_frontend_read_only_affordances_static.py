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


def test_assignment_controls_use_plant_and_plot_capabilities() -> None:
    data_tables = _read("frontend/src/components/dataTables.ts")
    plant_card = _read("frontend/src/components/plantCard.ts")
    drawer = _read("frontend/src/components/drawer.ts")
    bottom_sheet = _read("frontend/src/components/bottomSheet.ts")
    interactions = _read("frontend/src/components/plotInteractions.ts")
    app = _read("frontend/src/app.ts")

    assert "const canAssign = plant.can_assign;" in data_tables
    assert "if (onPlace && canAssign)" in data_tables
    assert 'className = "plot-link-action"' not in data_tables
    assert 'moveBtn.className = "plot-chip-move";' in _read("frontend/src/components/overlays.ts")
    assert "options.canWrite !== false" in plant_card
    assert "options.canAssign !== false" in plant_card
    assert "plant.can_assign" in plant_card
    assert "canAssign?: boolean;" in drawer
    assert "canAssign?: boolean;" in bottom_sheet
    assert drawer.count("canAssign: params.canAssign") == 2
    assert bottom_sheet.count("canAssign: params.canAssign") == 2
    assert "params.canAssign !== false" in drawer
    assert "params.canAssign !== false" in bottom_sheet
    assert "const canAssign =" in interactions
    assert (
        "state.plots.find((plot) => plot.plot_id === plotId)?.can_assign === true" in interactions
    )
    assert "const assignablePlants = plants.filter" in interactions
    assert "can_assign?: boolean" in interactions
    assert "if (!plot?.can_assign || plantCanAssign === false)" in interactions
    assert "if (!plot?.can_assign || !plant?.can_assign)" in interactions
    assert "if (!sourcePlot?.can_assign || !destinationPlot?.can_assign)" in app
    assert "(plot) => plot.archived_at_ms == null && plot.can_assign" in app
    assert (
        "if (!plant.can_assign || !sourcePlot?.can_assign || !ensureWriteAccess()) return;" in app
    )
    assert "if (!plant.can_assign || !ensureWriteAccess()) return;" in app
    assert app.count("if (destinations.length === 0) return;") == 2


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
    weather_feature = _read("frontend/src/features/weatherFeature.ts")
    app = _read("frontend/src/app.ts")
    main = _read("gardenops/main.py")

    assert "createWeatherAlertCardMarkup(alert, true)" in weather
    assert weather.count('addEventListener("click", callbacks.onCheckWeather)') == 2
    assert "canWrite: boolean;" in weather
    assert "syncWeatherDashboardWriteAccess" in weather
    assert "canWriteWeather" not in weather
    assert "{ canWrite: ctx.canWrite() }," in weather_feature
    assert "export function syncWeatherWriteAccess" in weather_feature
    assert "syncWeatherWriteAccess();" in app
    assert 'weather_alert_prefix = "/api/weather/alerts/"' in main
    assert "return alert_id.isdigit()" in main


def test_weather_dashboard_keeps_a_refresh_action_when_forecast_days_are_empty() -> None:
    weather = _read("frontend/src/components/weather.ts")

    assert "if (summary.forecast_days.length > 0)" in weather
    assert "const action = summary.forecast_available" in weather
    assert "weatherCheckActionMarkup(options, action)" in weather
    assert 'weatherCheckActionMarkup(options, t("weather.refresh"))' in weather
    assert 'weatherCheckActionMarkup(options, t("weather.check"))' in weather
