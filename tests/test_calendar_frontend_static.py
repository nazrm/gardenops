from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _function_body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_calendar_task_actions_require_a_writable_event() -> None:
    calendar_tab = _read("frontend/src/tabs/calendarTab.ts")
    mutability_guard = _function_body(
        calendar_tab,
        "function canMutateCalendarTask",
        "function calendarTaskForSnooze",
    )
    detail_body = _function_body(
        calendar_tab,
        "function renderDetail",
        "async function runTaskAction",
    )
    action_body = _function_body(
        calendar_tab,
        "async function runTaskAction",
        "async function enqueueOfflineCalendarTaskAction",
    )

    assert 'event.kind === "task"' in mutability_guard
    assert "!event.read_only" in mutability_guard
    assert "ctx.canWrite()" in mutability_guard
    assert "if (canMutateCalendarTask(event))" in detail_body
    assert "if (!canMutateCalendarTask(event)) return false;" in action_body


def test_calendar_state_is_scoped_to_the_active_garden_and_invalidated_on_switch() -> None:
    app = _read("frontend/src/app.ts")
    calendar_tab = _read("frontend/src/tabs/calendarTab.ts")

    assert "calendarTabModule?.resetCalendarForGardenSwitch();" in app
    assert "getActiveGardenContext" in calendar_tab
    assert "let calendarRequestGeneration = 0;" in calendar_tab
    assert "function isCurrentCalendarRequest" in calendar_tab
    assert "function isCurrentCalendarEventsRequest" in calendar_tab
    assert "function isCurrentCalendarEvent(" in calendar_tab
    assert "export function resetCalendarForGardenSwitch" in calendar_tab
    assert "currentEventsById.clear();" in calendar_tab
    assert "preferencesLoaded = false;" in calendar_tab
    assert "subscriptions = [];" in calendar_tab
    assert "selectedEventId = null;" in calendar_tab
    assert "fetchPreferences(" in calendar_tab
    assert "refreshSubscriptions(" in calendar_tab


def test_calendar_view_changes_persist_as_personal_preferences_for_viewers() -> None:
    calendar_tab = _read("frontend/src/tabs/calendarTab.ts")
    persist_body = _function_body(
        calendar_tab,
        "async function persistPreferences",
        "async function fetchPreferences",
    )
    instance_body = _function_body(
        calendar_tab,
        "function ensureCalendarInstance",
        "async function changeView",
    )

    assert "if (!ctx.canWrite()) return;" not in persist_body
    assert "await updateCalendarPreferencesApi" in persist_body
    assert "calendarPreferencesCache.set(gardenId" in persist_body
    assert 't("calendar.preferences_save_failed"' in persist_body
    assert "ctx.showToast(" in persist_body
    assert "calendar.render();" not in instance_body
    assert "let calendarRendered = false;" in calendar_tab


def test_calendar_cold_offline_state_does_not_render_false_empty_events() -> None:
    calendar_tab = _read("frontend/src/tabs/calendarTab.ts")
    layout = _read("frontend/src/components/layout.ts")

    assert "const calendarPreferencesCache = new Map" in calendar_tab
    assert "const calendarEventsCache = new Map" in calendar_tab
    assert 'setCalendarDataState("unavailable")' in calendar_tab
    assert 'setCalendarDataState("cached")' in calendar_tab
    assert 'root.hidden = state === "unavailable"' in calendar_tab
    assert 'id="calendar-data-state"' in layout


def test_calendar_uses_the_shared_date_dialog_and_correction_notice() -> None:
    calendar_tab = _read("frontend/src/tabs/calendarTab.ts")

    assert "openTaskDateDialog" in calendar_tab
    assert "getTaskSnoozeCorrectionNotice" in calendar_tab
    assert "durationMs: notice.durationMs" in calendar_tab
    assert 't("tasks.snooze_change_date")' in calendar_tab
    assert "window.prompt" not in calendar_tab


def test_calendar_snooze_correction_keeps_a_stable_task_target_after_refresh() -> None:
    calendar_tab = _read("frontend/src/tabs/calendarTab.ts")
    correction_body = _function_body(
        calendar_tab,
        "async function snoozeCalendarTask",
        "function openCalendarTaskDateDialog",
    )

    assert "const task = await loadCalendarTaskForSnooze(event);" in correction_body
    assert "calendarTaskActionTarget(event, task)" in correction_body
    assert "openCalendarTaskSnoozeDateDialogForTarget(target" in correction_body
    assert "currentEventsById.get(event.id)" not in correction_body


def test_calendar_export_url_carries_the_active_garden_context() -> None:
    api = _read("frontend/src/services/api.ts")
    export_url_builder = _function_body(
        api,
        "export function buildCalendarExportUrl",
        "export async function listCalendarSubscriptionsApi",
    )

    assert "return `/api/calendar/export.ics?${query.toString()}`;" in export_url_builder
    assert 'query.set("garden_id", String(activeGardenId))' in export_url_builder
    assert "if (activeGardenId !== null)" in export_url_builder


def test_calendar_export_uses_europe_oslo_local_calendar_dates() -> None:
    calendar_tab = _read("frontend/src/tabs/calendarTab.ts")
    snooze_policy = _read("frontend/src/features/taskSnoozePolicy.ts")
    export_body = _function_body(
        calendar_tab,
        "function exportCalendar",
        "function ensureCalendarInstance",
    )
    local_date_helper = _function_body(
        snooze_policy,
        "export function formatLocalDate",
        "export function taskSnoozeMaximumDate",
    )

    oslo_date = datetime(2026, 7, 14, 22, tzinfo=UTC).astimezone(
        ZoneInfo("Europe/Oslo"),
    )

    assert oslo_date.strftime("%Y-%m-%d") == "2026-07-15"
    assert "formatLocalDate(view.activeStart)" in export_body
    assert "formatLocalDate(view.activeEnd)" in export_body
    assert "toISOString" not in export_body
    assert "date.getFullYear()" in local_date_helper
    assert "date.getMonth()" in local_date_helper
    assert "date.getDate()" in local_date_helper
    assert "toISOString" not in local_date_helper


def test_calendar_offline_snooze_requires_a_complete_cached_task_policy() -> None:
    calendar_tab = _read("frontend/src/tabs/calendarTab.ts")
    snooze_loader = _function_body(
        calendar_tab,
        "async function loadCalendarTaskForSnooze",
        "interface CalendarTaskActionTarget",
    )
    action_body = _function_body(
        calendar_tab,
        "async function runCalendarTaskActionForTarget",
        "async function enqueueOfflineCalendarTaskAction",
    )

    assert 'import { getCachedTodayTasks } from "../services/taskCache";' in calendar_tab
    assert "const calendarSnoozeTaskCache = new Map<string, GardenTask>();" in calendar_tab
    assert "function isCompleteCalendarSnoozeTask" in calendar_tab
    assert "task.rule_source" in calendar_tab
    assert "task.metadata" in calendar_tab
    assert "task.updated_at_ms === event.updated_at_ms" in calendar_tab
    assert "getCachedTodayTasks(gardenId)" in calendar_tab
    assert "getCachedCalendarTaskForSnooze(event)" in snooze_loader
    assert 'ctx.showToast(t("calendar.offline_unavailable"), "error")' in snooze_loader
    assert "function calendarTaskForSnooze" not in calendar_tab
    assert 'rule_source: ""' not in calendar_tab
    assert "metadata: {}" not in calendar_tab
    assert 'body.action === "snooze" && !target.offlineSnoozeTask' in action_body
    assert "withTaskActionRevision(target.taskRevision, body)" in action_body


def test_calendar_subscription_refreshes_before_clipboard_failure_fallback() -> None:
    calendar_tab = _read("frontend/src/tabs/calendarTab.ts")
    copy_body = _function_body(
        calendar_tab,
        "async function copyCreatedCalendarFeed",
        "async function createSubscription",
    )
    create_body = _function_body(
        calendar_tab,
        "async function createSubscription",
        "function exportCalendar",
    )

    assert "await navigator.clipboard.writeText(feedUrl);" in copy_body
    assert "catch {" in copy_body
    assert 'ctx.showToast(t("calendar.feed_copy_prompt"), "error");' in copy_body
    assert 'await promptDialog(t("calendar.feed_copy_prompt"), feedUrl);' in copy_body
    assert create_body.count("createCalendarSubscriptionApi") == 1
    assert create_body.index("await refreshSubscriptions(request);") < create_body.index(
        "await copyCreatedCalendarFeed(feedUrl, request);",
    )


def test_dev_server_proxies_calendar_subscription_feeds() -> None:
    vite_config = _read("frontend/vite.config.ts")

    assert '"/api": apiProxyTarget' in vite_config
    assert '"/calendar/subscriptions": apiProxyTarget' in vite_config


def test_chip_input_closes_options_after_a_selection() -> None:
    chip_input = _read("frontend/src/components/chipInput.ts")
    close_body = _function_body(
        chip_input,
        "function closeDropdown",
        "function renderChips",
    )
    selection_body = _function_body(
        chip_input,
        "function selectItem",
        "function highlightOption",
    )

    assert "dropdown.hidden = true;" in close_body
    assert 'input.setAttribute("aria-expanded", "false");' in close_body
    assert 'input.removeAttribute("aria-activedescendant");' in close_body
    assert "closeDropdown();" in selection_body
    assert "updateDropdown();" not in selection_body


def test_desktop_global_plant_search_has_an_accessible_name() -> None:
    layout = _read("frontend/src/components/layout.ts")
    search_markup = layout.split('id="global-plant-search"', 1)[1].split("/>", 1)[0]

    assert 'aria-label="${t("nav.find_plant_on_map")}"' in search_markup
    assert 'data-i18n-aria-label="nav.find_plant_on_map"' in search_markup
