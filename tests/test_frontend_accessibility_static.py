import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LAYOUT_INPUT_KEYS = {
    "plants-search": "plants.search_placeholder",
    "inventory-search": "inventory.search_placeholder",
    "journal-filter-search": "journal.search_placeholder",
    "journal-filter-actor": "journal.actor_placeholder",
    "harvest-filter-from": "harvest.filter_from",
    "harvest-filter-to": "harvest.filter_to",
    "care-search": "care.search_placeholder",
}

ADMIN_INPUT_KEYS = {
    "adm-inv-username": "admin_panel.placeholder_username",
    "adm-inv-ttl": "admin_panel.placeholder_ttl",
    "adm-new-username": "admin_panel.placeholder_username",
    "adm-new-password": "admin_panel.placeholder_password",
    "adm-user-inv-username": "admin_panel.placeholder_username",
    "adm-user-inv-ttl": "admin_panel.placeholder_ttl",
    "adm-audit-garden": "admin_panel.placeholder_garden_id",
    "adm-audit-actor": "admin_panel.placeholder_actor",
    "adm-audit-path": "admin_panel.placeholder_path_prefix",
    "adm-audit-status": "common.status",
}

PROVIDER_SECRET_KEYS = (
    "openai_api_key",
    "anthropic_api_key",
    "plantnet_api_key",
    "shademap_api_key",
)

CALENDAR_FILTER_INPUTS = (
    ("calendar.plant_filter_label", "calendar-plant-filter-input"),
    ("calendar.plot_filter_label", "calendar-plot-filter-input"),
    ("calendar.zone_filter_label", "calendar-zone-filter-input"),
)


def _read_frontend(path: str) -> str:
    return (ROOT / "frontend" / "src" / path).read_text(encoding="utf-8")


def _input_markup(source: str, input_id: str) -> str:
    match = re.search(
        rf'<input\b(?=[^>]*\bid="{re.escape(input_id)}")[^>]*>',
        source,
    )
    assert match, f"missing input {input_id}"
    return match.group(0)


def _assert_localized_aria_label(markup: str, key: str) -> None:
    expected = 'aria-label="' + "$" + '{t("' + key + '")}"'
    assert expected in markup
    assert f'data-i18n-aria-label="{key}"' in markup


def test_all_reported_accessibility_name_defects_are_covered() -> None:
    assert (
        len(LAYOUT_INPUT_KEYS)
        + 1
        + len(ADMIN_INPUT_KEYS)
        + len(PROVIDER_SECRET_KEYS) * 2
        + len(CALENDAR_FILTER_INPUTS)
    ) == 29


def test_static_filter_inputs_have_localized_accessible_names() -> None:
    layout = _read_frontend("components/layout.ts")

    for input_id, key in LAYOUT_INPUT_KEYS.items():
        _assert_localized_aria_label(_input_markup(layout, input_id), key)


def test_indoor_search_has_a_localized_accessible_name() -> None:
    indoor_tab = _read_frontend("tabs/indoorTab.ts")
    i18n = _read_frontend("core/i18n.ts")

    assert 'searchInput.setAttribute("aria-label", t("indoor.search_placeholder"));' in indoor_tab
    assert 'searchInput.dataset["i18nAriaLabel"] = "indoor.search_placeholder";' in indoor_tab
    assert 'root.querySelectorAll<HTMLElement>("[data-i18n-aria-label]")' in i18n
    assert 'element.setAttribute("aria-label", t(key));' in i18n


def test_admin_inputs_and_provider_secret_actions_have_localized_names() -> None:
    admin_panel = _read_frontend("components/adminPanel.ts")
    i18n = _read_frontend("core/i18n.ts")

    for input_id, key in ADMIN_INPUT_KEYS.items():
        _assert_localized_aria_label(_input_markup(admin_panel, input_id), key)

    assert 'admin.provider_keys.replace_secret", { provider: t(labelKey) }' in admin_panel
    assert 'admin.provider_keys.clear_secret", { provider: t(labelKey) }' in admin_panel
    for key in PROVIDER_SECRET_KEYS:
        assert f'key: "{key}"' in admin_panel

    for key in (
        "admin.provider_keys.replace_secret",
        "admin.provider_keys.clear_secret",
        "harvest.filter_from",
        "harvest.filter_to",
    ):
        assert i18n.count(f'"{key}":') == 2


def test_calendar_filter_chip_inputs_bind_their_visible_labels() -> None:
    calendar_tab = _read_frontend("tabs/calendarTab.ts")
    chip_input = _read_frontend("components/chipInput.ts")

    assert "inputId?: string;" in chip_input
    assert "labelEl.htmlFor = inputId;" in chip_input
    assert "input.id = inputId;" in chip_input
    for label_key, input_id in CALENDAR_FILTER_INPUTS:
        assert f'label: t("{label_key}"),\n    inputId: "{input_id}",' in calendar_tab


def test_mobile_toasts_clear_the_primary_navigation() -> None:
    styles = _read_frontend("style.css")
    mobile_toast_rule = re.search(
        r"@media \(max-width: 600px\) \{\s*#toast-container \{\s*"
        r"bottom: calc\(78px \+ env\(safe-area-inset-bottom, 0px\)\);",
        styles,
    )

    assert mobile_toast_rule


def test_task_form_uses_shared_modal_focus_and_explicit_exit_controls() -> None:
    tasks_tab = _read_frontend("tabs/tasksTab.ts")
    task_form = _read_frontend("components/tasks.ts")

    assert "createModal(" in tasks_tab
    assert "onCancel: close" in tasks_tab
    assert 'form.setAttribute("aria-labelledby", "task-form-title")' in task_form
    for control_id in (
        "task-form-type",
        "task-form-name",
        "task-form-description",
        "task-form-severity",
        "task-form-due",
    ):
        assert f'.id = "{control_id}"' in task_form
    assert "typeLabel.htmlFor = typeSelect.id" in task_form
    assert "cancelBtn.addEventListener(\"click\", onCancel)" in task_form


def test_plot_drawer_sheet_and_collapsibles_are_keyboard_dialogs() -> None:
    drawer = _read_frontend("components/drawer.ts")
    sheet = _read_frontend("components/bottomSheet.ts")

    assert 'const header = document.createElement("button")' in drawer
    assert 'header.setAttribute("aria-controls", bodyId)' in drawer
    assert 'header.setAttribute("aria-expanded", "true")' in drawer
    assert "if (body) body.hidden = collapsed" in drawer
    for source in (drawer, sheet):
        assert 'setAttribute("role", "dialog")' in source
        assert 'setAttribute("aria-modal", "true")' in source
        assert "trapFocus(" in source
        assert 'e.key !== "Escape"' in source
        assert "activeReturnFocus" in source
    assert 'const handleBar = document.createElement("button")' in sheet
    assert 'handleBar.setAttribute("aria-label", t("plot_drawer.resize_sheet"))' in sheet
