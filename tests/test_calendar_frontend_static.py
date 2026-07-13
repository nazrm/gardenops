from pathlib import Path

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
