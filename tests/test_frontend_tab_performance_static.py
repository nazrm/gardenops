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


def test_plants_cache_is_prefetched_after_initial_map_load() -> None:
    app = _read_frontend("app.ts")
    bootstrap_body = app.split("async function bootstrapApp", 1)[1].split(
        "async function checkOnboardingNeeded",
        1,
    )[0]

    assert "requestPlantsCachePrefetchAfterPaint" in app
    assert "PLANTS_CACHE_PREFETCH_DELAY_MS" in app
    assert "requestPlantsCachePrefetchAfterPaint();" in bootstrap_body
    prefetch_body = app.split("function requestPlantsCachePrefetchAfterPaint", 1)[1].split(
        "async function fetchPlantDetails",
        1,
    )[0]
    assert "renderPlantsTable();" in prefetch_body


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
