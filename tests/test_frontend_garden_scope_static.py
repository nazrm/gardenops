from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _function_body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_snapshot_restore_refreshes_plants_after_a_coherent_map_refresh() -> None:
    app = _read("frontend/src/app.ts")
    snapshots = _read("frontend/src/features/snapshotsFeature.ts")
    refresh_body = _function_body(
        app,
        "async function refreshRestoredSnapshotState",
        "async function fetchPlantDetails",
    )

    assert "refreshRestoredSnapshotState(): Promise<void>;" in snapshots
    assert "await ctx.refreshRestoredSnapshotState();" in snapshots
    assert "invalidatePlantsCache();" in refresh_body
    assert "const plantsLoadPromise = ensurePlantsCacheLoaded();" in refresh_body
    assert "await refreshMapState({ coherent: true });" in refresh_body
    assert "await plantsLoadPromise;" in refresh_body
    assert "renderPlantsTable();" in refresh_body
    assert "plantsCacheRequestVersion" in app


def test_indoor_state_cannot_apply_an_old_garden_request() -> None:
    app = _read("frontend/src/app.ts")
    indoor = _read("frontend/src/tabs/indoorTab.ts")
    clear_body = _function_body(
        app,
        "function clearGardenScopedStateForSwitch",
        "async function switchGarden",
    )

    assert "let indoorGardenId: number | null = null;" in indoor
    assert "let indoorRequestVersion = 0;" in indoor
    assert "export function resetIndoorState(): void" in indoor
    assert "requestVersion === indoorRequestVersion" in indoor
    assert "gardenId === getActiveGardenContext()" in indoor
    assert "resetIndoorState();" in clear_body
    assert "setIndoorPlotId(indoorPlot.plot_id, requestGardenId);" in app


def test_admin_garden_settings_preserve_drafts_and_reject_same_garden_stale_requests() -> None:
    admin = _read("frontend/src/components/adminPanel.ts")
    load_body = _function_body(
        admin,
        "async function loadGardenSettings",
        "async function loadSystem",
    )
    repaint_body = _function_body(
        admin,
        "async function loadAndRepaintSection",
        "function hasGardenSettingsDraft",
    )

    assert "requestVersion === gardenSettingsRequestVersion" in load_body
    assert "gardenContextFn?.().activeGardenId === requestGardenId" in load_body
    assert "!hasGardenSettingsDraft()" in repaint_body
    assert "gardenSettingsRequestVersion += 1;" in admin


def test_notifications_reset_and_reload_when_the_active_garden_changes() -> None:
    app = _read("frontend/src/app.ts")
    notifications = _read("frontend/src/features/notificationsFeature.ts")
    switch_body = _function_body(
        app,
        "async function switchGarden",
        "async function refreshGardenDataForCurrentContext",
    )

    assert "export function resetNotificationsForCurrentGarden(): void" in notifications
    assert "closeNotificationPanel();" in notifications
    assert "notificationItems = [];" in notifications
    assert "void loadNotificationCount(request);" in notifications
    assert "if (notificationsInitialized)" in notifications
    assert "resetNotificationsForCurrentGarden();" in switch_body


def test_notifications_sync_after_initial_garden_resolution_and_before_open() -> None:
    app = _read("frontend/src/app.ts")
    notifications = _read("frontend/src/features/notificationsFeature.ts")
    refresh_body = _function_body(
        app,
        "async function refreshGardenContext",
        "function updateShadeMapAvailabilityUi",
    )
    toggle_body = _function_body(
        notifications,
        "async function toggleNotificationPanel",
        "async function loadNotifications",
    )

    assert "export function syncNotificationsForCurrentGarden(): void" in notifications
    assert "notificationGardenId === getActiveGardenContext()" in notifications
    assert "syncNotificationsForCurrentGarden();" in refresh_body
    assert "syncNotificationsForCurrentGarden();" in toggle_body


def test_weather_and_plot_alert_requests_cannot_apply_to_a_new_garden() -> None:
    app = _read("frontend/src/app.ts")
    weather = _read("frontend/src/features/weatherFeature.ts")
    clear_body = _function_body(
        app,
        "function clearGardenScopedStateForSwitch",
        "async function switchGarden",
    )
    plot_alert_body = _function_body(
        app,
        "async function loadPlotAlerts",
        "function requestPlotAlertsAfterPaint",
    )

    assert "export function resetWeatherForCurrentGarden(): void" in weather
    assert "request.gardenId === getActiveGardenContext()" in weather
    assert "if (!isCurrentWeatherRequest(request)) return;" in weather
    assert "weatherCacheRequestVersion += 1;" in clear_body
    assert "resetWeatherForCurrentGarden();" in clear_body
    assert "plotAlertsRequestVersion += 1;" in clear_body
    assert "requestVersion !== plotAlertsRequestVersion" in plot_alert_body
    assert "!isCurrentGardenRequest(requestGardenId)" in plot_alert_body
    assert "plotAlertsLoadPromise === loadPromise" in plot_alert_body


def test_admin_garden_settings_cannot_apply_an_old_garden_request() -> None:
    admin = _read("frontend/src/components/adminPanel.ts")
    body = _function_body(
        admin,
        "async function loadGardenSettings",
        "async function loadSystem",
    )

    assert "const requestGardenId = ctx.activeGardenId;" in body
    assert "gardenContextFn?.().activeGardenId === requestGardenId" in body
    assert body.count("if (!isCurrentRequest()) return false;") >= 6
    assert "getGardenSettingsApi(requestGardenId)" in body
    assert "getGardenMembershipsApi(requestGardenId)" in body


def test_mobile_map_sheets_are_inert_when_closed_and_manage_focus() -> None:
    app = _read("frontend/src/app.ts")
    layout = _read("frontend/src/components/layout.ts")

    assert layout.count("data-mobile-map-sheet-initial-focus") == 4
    assert 'id="mobile-map-layouts-sheet"' in layout
    assert 'id="mobile-map-tools-sheet"' in layout
    assert "inert>" in layout
    assert 'sheet.toggleAttribute("inert", !isOpen);' in app
    assert "function focusMobileMapSheet" in app
    assert "function restoreMobileMapSheetFocus" in app
    assert "function trapMobileMapSheetFocus" in app
    assert "mobileMapSheetFocusReturnTarget" in app


def test_mobile_map_editor_opens_an_operable_layers_sheet() -> None:
    app = _read("frontend/src/app.ts")

    assert "editorAvailable: true" in app
    assert 'setMobileMapSheetOpen("map-layers-panel")' in app
    assert 'showToast(t("map.desktop_only")' not in app


def test_mobile_utility_sheet_is_inert_and_traps_focus() -> None:
    app = _read("frontend/src/app.ts")
    layout = _read("frontend/src/components/layout.ts")
    notifications = _read("frontend/src/features/notificationsFeature.ts")

    assert 'id="mobile-utility-sheet"' in layout
    assert 'aria-hidden="true" aria-labelledby="mobile-utility-title" inert' in layout
    assert "data-mobile-utility-initial-focus" in layout
    assert 'sheet.toggleAttribute("inert", !open);' in app
    assert 'sheet.setAttribute("aria-modal", "true");' in app
    assert "function trapMobileUtilityFocus" in app
    assert "if (trapMobileUtilityFocus(e)) return;" in app
    assert "mobileUtilityFocusReturnTarget" in app
    assert "panel.tabIndex = -1;" in notifications
    assert "panel.focus();" in notifications


def test_garden_switch_blocks_old_controls_before_async_refresh() -> None:
    app = _read("frontend/src/app.ts")
    switch_body = _function_body(
        app,
        "async function switchGarden",
        "async function refreshGardenDataForCurrentContext",
    )

    assert "gardenContextAvailable = false;" in switch_body
    assert "applyWriteAccessUi();" in switch_body
    assert "setMobileMapSheetOpen(null);" in switch_body
    assert "setGardenSwitchPending(true);" in switch_body
    assert 'root.toggleAttribute("inert", pending);' in app
    assert "if (gardenSwitchPending) {" in app


def test_map_refresh_has_a_fast_path_and_a_coherent_restore_path() -> None:
    app = _read("frontend/src/app.ts")
    refresh_body = _function_body(
        app,
        "async function refreshMapState",
        "async function createMapObjectFromSelection",
    )

    assert "const plotsPromise = fetchPlots(2, fetchOptions);" in refresh_body
    assert "await plotsPromise;" in refresh_body
    assert "void optionalStatePromise.then(() => {" in refresh_body
    assert "if (options.coherent) {" in refresh_body
    assert "await Promise.all([plotsPromise, optionalStatePromise]);" in refresh_body
