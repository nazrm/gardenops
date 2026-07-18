import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACCESSIBILITY_INVENTORY = ROOT / "tests" / "accessibility_expectations.yaml"

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


def test_phase_eight_state_inventory_is_complete_and_machine_readable() -> None:
    payload = yaml.safe_load(ACCESSIBILITY_INVENTORY.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    states = payload["states"]
    assert isinstance(states, list) and len(states) >= 9
    required_variants = {
        "populated",
        "empty",
        "validation-error",
        "busy",
        "recoverable-error",
        "dialog",
        "read-only",
        "mobile",
    }
    observed_variants = {variant for state in states for variant in state["variants"]}
    assert required_variants <= observed_variants
    for state in states:
        assert state["id"]
        assert state["surfaces"]
        assert state["route"]
        assert state["focus"]
        assert isinstance(state["automated_scan"], bool)


def test_invitation_loading_and_chat_failures_expose_live_semantics() -> None:
    auth_gate = _read_frontend("features/authGate.ts")
    analysis = _read_frontend("tabs/analysisTab.ts")

    assert 'loadingDiv.setAttribute("role", "status")' in auth_gate
    assert 'loadingDiv.setAttribute("aria-live", "polite")' in auth_gate
    assert 'loadingDiv.setAttribute("aria-busy", "true")' in auth_gate
    assert 'spinner.setAttribute("aria-hidden", "true")' in auth_gate
    assert 'loading.setAttribute("role", "status")' in analysis
    assert 'loading.setAttribute("aria-live", "polite")' in analysis
    assert 'errBubble.setAttribute("role", "alert")' in analysis
    assert 'errBubble.setAttribute("aria-live", "assertive")' in analysis


def test_primary_navigation_uses_standard_navigation_semantics() -> None:
    layout = _read_frontend("components/layout.ts")
    tabs = _read_frontend("components/tabs.ts")
    app = _read_frontend("app.ts")
    styles = _read_frontend("style.css")

    assert '<nav class="top-nav desktop-top-nav" aria-label="${t("nav.main_sections")}"' in layout
    assert 'role="tablist" aria-label="${t("nav.main_sections")}"' not in layout
    assert 'id="top-tab-map" class="top-tab active" data-tab="map" aria-current="page"' in layout
    assert 'id="top-tab-garden" class="top-tab" data-tab="garden" data-i18n="nav.garden"' in layout
    assert 'role="tabpanel" aria-labelledby="top-tab-map"' not in layout
    assert 'btn.setAttribute("aria-current", "page")' in app
    assert 'btn.removeAttribute("aria-current")' in app
    assert 'addEventListener("keydown"' not in tabs
    assert ".mobile-tab-btn:focus {\n  outline: 2px solid var(--focus-ring);" in styles


def test_shell_has_one_desktop_heading_and_decorative_brand_images() -> None:
    layout = _read_frontend("components/layout.ts")
    app = _read_frontend("app.ts")
    styles = _read_frontend("style.css")

    assert '<h1 class="desktop-view-title" id="desktop-view-title">${t("nav.map")}</h1>' in layout
    assert layout.count('class="app-brand-logo"') == 1
    assert layout.count('class="mobile-header-logo"') == 1
    assert layout.count('alt=""') >= 2
    assert '"#mobile-view-title, #desktop-view-title"' in app
    assert ".desktop-view-title {" in styles
    assert "@media (max-width: 960px) {\n  .desktop-view-title {\n    display: none;" in styles


def test_phase_eight_controls_use_contrast_safe_foregrounds() -> None:
    styles = _read_frontend("style.css")

    top_meta = styles.split(".top-meta {", 1)[1].split("}", 1)[0]
    active_map_object_toggle = styles.split(".map-object-toggle.active {", 1)[1].split("}", 1)[0]
    primary_attention_action = (
        styles.split(".attention-today-action--primary {", 1)[1].split("}", 1)[0]
    )

    assert "opacity" not in top_meta
    assert "color: var(--text-1);" in active_map_object_toggle
    assert "background: var(--brand);" in primary_attention_action
    assert "color: var(--text-inv);" in primary_attention_action


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


def test_passive_toasts_do_not_block_garden_controls() -> None:
    styles = _read_frontend("style.css")
    toast = _read_frontend("components/toast.ts")
    toast_rule = styles.rsplit(".toast {", 1)[1].split("}", 1)[0]

    assert "pointer-events: none;" in toast_rule
    assert ".toast-interactive {\n  pointer-events: auto;" in styles
    assert 'toast.classList.add("toast-interactive")' in toast


def test_coarse_pointer_notification_and_completion_controls_have_row_sized_targets() -> None:
    styles = _read_frontend("style.css")
    notifications = _read_frontend("components/notifications.ts")

    assert 'headerActions.className = "notification-panel-header-actions";' in notifications
    assert "@media (pointer: coarse)" in styles
    assert ".notification-mark-all-btn," in styles
    assert ".notification-item-dismiss" in styles
    assert "min-width: 44px;" in styles
    assert "min-height: 44px;" in styles
    assert ".notification-panel-header-actions," in styles
    assert ".notification-item-actions {\n    flex-direction: row;" in styles
    assert ".task-completion-list label {\n    min-height: 44px;" in styles
    assert "flex-direction: row;" in styles


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
    assert 'cancelBtn.addEventListener("click", onCancel)' in task_form


def test_plot_drawer_sheet_and_collapsibles_are_keyboard_dialogs() -> None:
    drawer = _read_frontend("components/drawer.ts")
    sheet = _read_frontend("components/bottomSheet.ts")
    map_view = _read_frontend("components/mapView.ts")

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
        assert "activeReturnPlotId" in source
        assert "document.activeElement !== document.body" in source
        assert "document.activeElement !== document.documentElement" in source
        assert '`.plot[data-plot-id="${CSS.escape(returnPlotId)}"]`' in source
        assert "focusTarget();" in source
        assert "window.requestAnimationFrame(focusTarget)" in source
    assert 'const handleBar = document.createElement("button")' in sheet
    assert 'handleBar.setAttribute("aria-label", t("plot_drawer.resize_sheet"))' in sheet
    assert "el.tabIndex = 0" in map_view
    assert 'el.setAttribute("role", "button")' in map_view
    assert 'e.key !== "Enter" && e.key !== " "' in map_view
