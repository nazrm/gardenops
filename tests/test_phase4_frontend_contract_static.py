from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_phase4_tabs_reject_stale_garden_requests_and_reset_state() -> None:
    app = _read("frontend/src/app.ts")
    inventory = _read("frontend/src/tabs/inventoryTab.ts")
    procurement = _read("frontend/src/tabs/procurementTab.ts")
    statistics = _read("frontend/src/tabs/statisticsTab.ts")
    care = _read("frontend/src/tabs/careTab.ts")
    search = _read("frontend/src/components/globalSearch.ts")

    for source, generation, current_check, reset in (
        (
            inventory,
            "inventoryRequestGeneration",
            "isCurrentInventoryRequest(request)",
            "resetInventoryForGardenSwitch",
        ),
        (
            procurement,
            "procurementRequestGeneration",
            "isCurrentProcurementRequest(request)",
            "resetProcurementForGardenSwitch",
        ),
        (
            care,
            "careRequestGeneration",
            "isCurrentCareGarden(gardenId)",
            "resetCareForGardenSwitch",
        ),
    ):
        assert generation in source
        assert current_check in source
        assert f"export function {reset}" in source
        assert f"{reset}();" in app

    assert "statisticsRequestGeneration" in statistics
    assert "isCurrentStatisticsRequest(request)" in statistics
    assert "resetStatisticsState();" in app
    assert "resetGlobalSearchForGardenSwitch();" in app
    assert "gardenId !== getActiveGardenContext()" in search


def test_phase4_mutations_are_garden_scoped_and_pending_locked() -> None:
    inventory_tab = _read("frontend/src/tabs/inventoryTab.ts")
    inventory_component = _read("frontend/src/components/inventory.ts")
    procurement_tab = _read("frontend/src/tabs/procurementTab.ts")
    procurement_component = _read("frontend/src/components/procurement.ts")
    planner = _read("frontend/src/components/planner.ts")

    assert "const inventoryPendingActions = new Set<string>();" in inventory_tab
    assert "{ gardenId }" in inventory_tab
    assert inventory_component.count('form.setAttribute("aria-busy", "true")') == 2
    assert inventory_component.count("if (pending) return;") >= 2

    assert "const procurementPendingActions = new Set<string>();" in procurement_tab
    assert "item.garden_id !== gardenId" in procurement_tab
    assert "runMutation(() => cbs.onTransition" in procurement_component
    assert 'form.setAttribute("aria-busy", "true")' in procurement_component

    assert "canStartWorkflows" in planner
    assert "if (canStart)" in planner
    assert "if (startBtn.disabled || selected.size === 0) return;" in planner
    assert 'card.setAttribute("aria-busy", "true")' in planner


def test_inventory_only_builds_the_active_responsive_surface() -> None:
    inventory = _read("frontend/src/tabs/inventoryTab.ts")
    inventory_component = _read("frontend/src/components/inventory.ts")

    assert 'window.matchMedia("(min-width: 961px)")' in inventory
    assert 'inventoryDesktopLayoutQuery.addEventListener("change"' in inventory
    assert "if (inventoryViewLoaded) renderInventoryView();" in inventory
    assert "const isDesktop = inventoryDesktopLayoutQuery.matches;" in inventory
    assert "if (isDesktop) {\n    if (mobileList) clearInventoryList(mobileList);" in inventory
    assert "} else if (mobileList) {\n    thead?.replaceChildren();" in inventory
    assert "renderVirtualList({" in inventory_component
    assert "estimateItemHeight: 190" in inventory_component
    assert "overscan: 4" in inventory_component


def test_phase4_fractional_inventory_and_accessible_procurement_forms() -> None:
    inventory_component = _read("frontend/src/components/inventory.ts")
    procurement_component = _read("frontend/src/components/procurement.ts")

    assert 'qtyInput.min = "0.000001";' in inventory_component
    assert 'qtyInput.step = "0.000001";' in inventory_component
    assert "qty = canonicalDecimalString(qtyInput.value);" in inventory_component
    assert 'compareDecimalStrings(qty, "0") <= 0' in inventory_component
    assert "Number(qtyInput.value)" not in inventory_component
    assert "parseInt(qtyInput.value" not in inventory_component

    for control_id in (
        "procurement-label",
        "procurement-type",
        "procurement-vendor",
        "procurement-quantity",
        "procurement-unit",
        "procurement-ordered-on",
        "procurement-notes",
    ):
        assert 'createFieldGroup(t("procurement.' in procurement_component
        assert f'"{control_id}"' in procurement_component
    assert 'setAttribute("for", controlId)' in procurement_component
    assert 'saveBtn.id = "procurement-save-btn";' in procurement_component


def test_phase4_inventory_precision_and_atomic_plant_contract() -> None:
    api = _read("frontend/src/services/api.ts")
    inventory_tab = _read("frontend/src/tabs/inventoryTab.ts")
    inventory_component = _read("frontend/src/components/inventory.ts")
    procurement_component = _read("frontend/src/components/procurement.ts")
    models = _read("frontend/src/core/models.ts")

    assert "export type DecimalString = string;" in api
    assert "BigInt(" in api
    assert "const DECIMAL_INTEGER_DIGITS = 14;" in api
    assert "enforceWireBounds && integer.length > DECIMAL_INTEGER_DIGITS" in api
    assert "normalizeDecimalString(value, false)" in api
    assert "export function addDecimalStrings" in api
    assert "export function compareDecimalStrings" in api
    assert "export function absoluteDecimalString" in api
    assert "quantity: DecimalString;" in api
    assert "delta: DecimalString;" in api
    assert "plantFromInventoryApi" in api
    assert "operationId: plantOperationId" in inventory_tab
    assert "addPlantToPlotApi" not in inventory_tab
    assert "await plantFromInventoryApi(" in inventory_tab
    assert "quantity: absoluteDecimalString(data.delta)" in inventory_tab
    assert "addDecimalStrings(sum, it.quantity)" in inventory_component
    assert "quantity: parseFloat(qtyInput.value)" not in procurement_component
    assert "quantity," in procurement_component
    assert "export interface ProcurementItem" in models
    procurement_model = models.split("export interface ProcurementItem", 1)[1].split("}", 1)[0]
    assert "quantity: string;" in procurement_model


def test_planner_preference_and_workflow_refresh_use_existing_surfaces() -> None:
    api = _read("frontend/src/services/api.ts")
    statistics = _read("frontend/src/tabs/statisticsTab.ts")
    app = _read("frontend/src/app.ts")

    assert "fetchPlannerGoalApi" in api
    assert "savePlannerGoalApi" in api
    assert 'apiFetch("/api/planner/goal", request)' in api
    assert "savePlannerGoalApi(goal || null" in statistics
    assert "plannerGoalSaveQueue" in statistics
    assert "ctx.loadTasks()" in statistics
    assert "loadTodayDashboard()" in statistics
    assert "ctx.refreshBadgeCounts()" in statistics
    assert "workflowRefreshHook?.()" in statistics
    assert "async function refreshWorkflowStartedSurfaces" in app
    assert "await loadCalendar();" in app


def test_care_catalog_and_export_states_are_bounded_and_honest() -> None:
    care = _read("frontend/src/tabs/careTab.ts")
    plant_search = _read("frontend/src/features/plantSearchFeature.ts")
    export_bar = _read("frontend/src/components/exportBar.ts")
    app = _read("frontend/src/app.ts")
    styles = _read("frontend/src/style.css")
    calendar = _read("frontend/src/tabs/calendarTab.ts")

    assert "CARE_GENERATION_MAX_REQUESTS" in care
    assert "result.next_cursor" in care
    assert "requestCount < CARE_GENERATION_MAX_REQUESTS" in care
    assert "careGenerationMessage" in care

    assert '"idle" | "loading" | "available" | "empty" | "degraded"' in plant_search
    assert 'catalogState = "degraded"' in plant_search
    assert "catalogError = getApiErrorMessage(err)" in plant_search
    assert "gardenId !== getActiveGardenContext()" in plant_search

    assert "async function downloadExport" in export_bar
    assert 'bar.setAttribute("aria-busy", "true")' in export_bar
    assert 'status.setAttribute("aria-live", "polite")' in export_bar
    for resource in ("journal", "inventory", "issues", "procurement"):
        assert f'openPrintable("{resource}", exportParams(params))' in app
    assert "window.print()" not in app
    assert "flex-wrap: wrap" in styles
    assert "min-height: 44px" in styles
    assert 'removeAttribute("data-calendar-export-ready")' in calendar
    assert 'setAttribute("data-calendar-export-ready", "true")' in calendar
