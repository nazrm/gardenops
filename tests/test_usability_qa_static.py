from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _function_body(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_issue_and_harvest_edit_labels_preserve_viewer_behavior() -> None:
    issues = _read("frontend/src/components/issues.ts")
    harvest = _read("frontend/src/components/harvest.ts")
    issue_card = _function_body(
        issues,
        "function createIssueCard(",
        "export function createIssueForm",
    )
    harvest_card = _function_body(
        harvest,
        "function createHarvestCard(",
        "export function createHarvestForm",
    )

    viewer_edit_label = (
        'cbs.canWrite === false\n      ? "issues.action_view_details"\n      : "common.edit"'
    )
    assert viewer_edit_label in issue_card
    assert 't("common.settings")' not in issue_card
    assert "if (cbs.canWrite !== false) {" in harvest_card
    assert 'editBtn.textContent = t("common.edit");' in harvest_card
    assert 't("common.settings")' not in harvest_card


def test_mobile_care_preview_localizes_field_labels_like_overlay() -> None:
    care = _read("frontend/src/components/careTable.ts")
    mobile_cards = _function_body(
        care,
        "export function renderCareMobileCards(",
        "export function clearCareMobileCards",
    )
    overlay = _function_body(
        care,
        "export function showCareOverlay(",
        "function dismissCareOverlay",
    )

    assert "label.textContent = t(field.label);" in mobile_cards
    assert "heading.textContent = t(field.label);" in overlay


def test_mobile_quick_actions_are_docked_without_losing_tab_exceptions() -> None:
    layout = _read("frontend/src/components/layout.ts")
    app = _read("frontend/src/app.ts")
    styles = _read("frontend/src/style.css")
    mobile_nav = layout.split('<nav class="mobile-tabbar"', 1)[1].split("</nav>", 1)[0]
    fab_styles = styles.split("Mobile FAB + Quick Actions", 1)[1].split(
        ".mobile-fab-backdrop",
        1,
    )[0]

    assert layout.count('id="mobile-fab"') == 1
    assert 'id="mobile-fab"' in mobile_nav
    assert "grid-template-columns: repeat(5, minmax(0, 1fr));" in fab_styles
    assert "position: static;" in fab_styles
    assert "position: fixed;" not in fab_styles
    assert 'mobileFab?.classList.toggle("mobile-fab--map-active", activeTab === "map")' in app
    assert 'mobileFab?.classList.toggle("mobile-fab--admin-active", activeTab === "admin")' in app
    assert "body.mobile-map-sheet-open .mobile-fab.mobile-fab--map-active" in styles
    assert ".mobile-fab.mobile-fab--admin-active {\n    display: none;" in styles


def test_plants_distinguish_filtered_exports_from_editable_round_trip() -> None:
    layout = _read("frontend/src/components/layout.ts")
    app = _read("frontend/src/app.ts")
    export_bar = _read("frontend/src/components/exportBar.ts")

    assert layout.count('id="export-csv-btn"') == 1
    assert 't("plants.export_editable_csv")' in layout
    assert "exportPlantsCsvApi" in app
    assert 't("plants.export_my_editable_csv")' in app
    assert 't("plants.export_editable_csv")' in app
    for control_id in (
        "plants-export-bar",
        "col-toggle-btn",
        "import-csv-btn",
        "export-csv-btn",
        "add-plant-btn",
    ):
        assert f'id="{control_id}"' in layout
    assert 't("exports.download_csv")' in export_bar
    assert 't("exports.download_json")' in export_bar
    assert 't("exports.print")' in export_bar


def test_garden_identity_width_override_stays_scoped_to_two_inputs() -> None:
    styles = _read("frontend/src/style.css")

    assert ".adm-input { width: 180px; }" in styles
    label_selector = (
        "#adm-garden-settings-form > .adm-form-stack > "
        "label:has(> :is(#adm-garden-name, #adm-garden-address))"
    )
    input_selector = "#adm-garden-settings-form :is(#adm-garden-name, #adm-garden-address)"
    assert styles.count(label_selector) == 1
    assert styles.count(input_selector) == 1
    garden_width_rule = styles.split(label_selector, 1)[1].split("}", 1)[0]
    assert "width: min(100%, 36rem);" in garden_width_rule
    assert "display: grid;" in garden_width_rule
    garden_input_rule = styles.split(input_selector, 1)[1].split("}", 1)[0]
    assert "width: 100%;" in garden_input_rule


def test_mobile_journal_footer_reserves_an_action_row() -> None:
    journal = _read("frontend/src/components/journal.ts")
    styles = _read("frontend/src/style.css")
    mobile_footer = styles.split(
        "@media (max-width: 600px) {\n  .journal-card-footer {",
        1,
    )[1].split(".journal-action-btn", 1)[0]

    assert 'footer.className = "journal-card-footer"' in journal
    assert 'actions.className = "journal-card-actions"' in journal
    assert "grid-template-columns: minmax(0, 1fr) auto;" in mobile_footer
    assert "grid-column: 1 / -1;" in mobile_footer
    assert "justify-content: flex-end;" in mobile_footer
