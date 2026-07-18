from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read_frontend(path: str) -> str:
    return (ROOT / "frontend" / "src" / path).read_text()


def test_logged_in_views_are_grouped_in_a_view_stack() -> None:
    layout = _read_frontend("components/layout.ts")

    assert 'id="view-stack"' in layout
    assert 'class="view-stack"' in layout
    assert layout.index('id="app-status"') < layout.index('id="view-stack"')
    assert layout.index('id="view-stack"') < layout.index('id="map-view"')
    assert layout.index('id="map-view"') < layout.index('id="plants-view"')


def test_map_standby_uses_content_visibility_not_hidden_display_none() -> None:
    app = _read_frontend("app.ts")
    styles = _read_frontend("style.css")

    assert "syncPrimaryViewVisibility" in app
    assert 'mapView.hidden = activeTab !== "map"' not in app
    assert ".view-map--standby" in styles
    standby_block = styles.split(".view-map--standby", 1)[1].split("}", 1)[0]
    assert "content-visibility: hidden" in standby_block
    assert "pointer-events: none" in standby_block
    assert "visibility: hidden" in standby_block
    assert "contain-intrinsic-size" in standby_block
    assert ".view-map--standby[inert]" in styles


def test_mobile_fab_map_state_is_scoped_without_a_body_tab_selector() -> None:
    app = _read_frontend("app.ts")
    styles = _read_frontend("style.css")
    navigation_body = app.split("function applyNavigationState", 1)[1].split(
        "function setActiveTab",
        1,
    )[0]

    assert "map-tab-active" not in app
    assert "body.map-tab-active" not in styles
    assert 'const mobileFab = document.getElementById("mobile-fab")' in navigation_body
    assert 'mobileFab?.classList.toggle("mobile-fab--map-active"' in navigation_body
    assert 'activeTab === "map"' in navigation_body
    assert 'mobileFab?.classList.toggle("mobile-fab--admin-active"' in navigation_body
    assert 'activeTab === "admin"' in navigation_body
    assert ".mobile-fab.mobile-fab--map-active" in styles
    assert "body.mobile-map-sheet-open .mobile-fab.mobile-fab--map-active" in styles
    assert ".mobile-fab.mobile-fab--admin-active" in styles


def test_mobile_notification_trigger_closes_utility_sheet() -> None:
    app = _read_frontend("app.ts")

    assert 'getElementById("mobile-notification-btn")?.addEventListener("click"' in app
    assert "setMobileUtilityOpen(false);" in app


def test_tab_switch_performance_script_reports_phase_metrics() -> None:
    script = (ROOT / "scripts" / "check_page_performance.cjs").read_text()

    assert "tabSwitchDetails" in script
    assert "collectCdpMetrics" in script
    assert "presentedMs" in script
    assert "readyMs" in script
    assert "playwrightActionMs" in script
    assert "scriptDurationMs" in script
    assert "styleLayoutDurationMs" in script
    assert "networkDuringSwitch" in script


def test_plot_alerts_are_deferred_off_the_tab_switch_path() -> None:
    app = _read_frontend("app.ts")
    navigation_body = app.split("function applyNavigationState", 1)[1].split(
        "function setActiveTab",
        1,
    )[0]

    assert "requestPlotAlertsAfterPaint" in app
    assert "PLOT_ALERTS_CACHE_MS" in app
    assert "requestPlotAlertsAfterPaint();" in navigation_body
    assert "loadPlotAlerts()" not in navigation_body


def test_plant_media_previews_are_deferred_off_the_render_path() -> None:
    app = _read_frontend("app.ts")
    render_body = app.split("function renderPlantsTable", 1)[1].split(
        "function updateSortIndicators",
        1,
    )[0]
    navigation_body = app.split("function applyNavigationState", 1)[1].split(
        "function setActiveTab",
        1,
    )[0]

    assert "requestPlantMediaPreviewsAfterPaint" in app
    assert "requestPlantMediaPreviewsAfterPaint(" in render_body
    assert "ensurePlantMediaPreviews(" not in render_body
    assert "plantMediaPreviewScheduleSeq += 1" in navigation_body


def test_initial_map_does_not_prefetch_full_plant_catalogue() -> None:
    app = _read_frontend("app.ts")
    bootstrap_body = app.split("async function bootstrapApp", 1)[1].split(
        "async function checkOnboardingNeeded",
        1,
    )[0]
    quick_actions = _read_frontend("features/quickActionsFeature.ts")
    plot_interactions = _read_frontend("components/plotInteractions.ts")

    assert "requestPlantsCachePrefetchAfterPaint" not in app
    assert "ensurePlantsCacheLoaded()" not in bootstrap_body
    assert "setPlantsViewLoading(true);" in app
    assert 't("common.loading")' in app
    assert "await ctx.ensurePlantsCacheLoaded();" in quick_actions
    assert "await cbs.ensurePlantsCacheLoaded();" in plot_interactions


def test_map_state_refresh_renders_plots_before_optional_map_state() -> None:
    app = _read_frontend("app.ts")
    refresh_body = app.split("async function refreshMapState", 1)[1].split(
        "async function createMapObjectFromSelection",
        1,
    )[0]

    assert "const plotsPromise = fetchPlots(2, fetchOptions);" in refresh_body
    assert "const optionalStatePromise = Promise.all([" in refresh_body
    assert "fetchLayoutState(fetchOptions)" in refresh_body
    assert "fetchMapObjects(fetchOptions)" in refresh_body
    assert "await plotsPromise;" in refresh_body
    assert "void optionalStatePromise.then(() => {" in refresh_body
    assert "if (options.coherent) {" in refresh_body
    assert "await Promise.all([plotsPromise, optionalStatePromise]);" in refresh_body


def test_garden_switch_hides_old_garden_until_refresh_completes() -> None:
    app = _read_frontend("app.ts")
    layout = _read_frontend("components/layout.ts")
    styles = _read_frontend("style.css")
    switch_body = app.split("async function switchGarden", 1)[1].split(
        "async function refreshGardenDataForCurrentContext",
        1,
    )[0]

    assert 'id="garden-switch-status"' in layout
    assert 'role="status"' in layout
    assert ".garden-switch-status" in styles
    assert "setGardenSwitchPending(true);" in switch_body
    assert "clearGardenScopedStateForSwitch();" in switch_body
    assert "expectedGardenId: nextGardenId" in switch_body
    assert "await refreshGardenDataForCurrentContext();" in switch_body
    assert "setGardenSwitchPending(false);" in switch_body


def test_weather_summary_is_deferred_off_insights_switch_path() -> None:
    app = _read_frontend("app.ts")
    refresh_body = app.split("async function refreshActiveNavigationContent", 1)[1].split(
        "function loadActiveNavigationContent",
        1,
    )[0]

    assert "requestWeatherAfterPaint" in app
    assert "WEATHER_SUMMARY_CACHE_MS" in app
    assert "requestWeatherAfterPaint();" in refresh_body
    assert "await loadWeather()" not in refresh_body
    weather_schedule = app.split("function requestWeatherAfterPaint", 1)[1].split(
        "let navigationLoadSeq", 1
    )[0]
    assert weather_schedule.count("window.requestAnimationFrame") == 2
    assert "window.setTimeout" not in weather_schedule
