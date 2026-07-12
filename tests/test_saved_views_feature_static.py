from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_saved_views_discards_stale_a_b_a_garden_requests() -> None:
    saved_views = (ROOT / "frontend/src/features/savedViewsFeature.ts").read_text(encoding="utf-8")

    assert "let savedViewsGardenId: number | null = null;" in saved_views
    assert "let savedViewsGardenVersion = 0;" in saved_views
    assert "let savedViewsLoadVersion = 0;" in saved_views
    assert "export function resetSavedViewsForCurrentGarden(): void" in saved_views
    assert "savedViewsGardenVersion += 1;" in saved_views
    assert "savedViewsLoadVersion += 1;" in saved_views
    assert "dropdown.hidden = true;" in saved_views
    assert "dropdown.replaceChildren();" in saved_views
    assert "request.gardenVersion === savedViewsGardenVersion" in saved_views
    assert "request.gardenId === getActiveGardenContext()" in saved_views
    assert "request.loadVersion === savedViewsLoadVersion" in saved_views
    assert "const request: SavedViewsLoadRequestContext" in saved_views
    assert "loadVersion: ++savedViewsLoadVersion," in saved_views
    assert saved_views.count("if (!isCurrentSavedViewsLoadRequest(request)) return false;") >= 2


def test_saved_views_reset_and_close_when_the_garden_selector_changes() -> None:
    saved_views = (ROOT / "frontend/src/features/savedViewsFeature.ts").read_text(encoding="utf-8")
    change_listener = saved_views.split('document.addEventListener("change",', 1)[1].split(
        'document.addEventListener("click",', 1
    )[0]

    assert "event.target instanceof HTMLSelectElement" in change_listener
    assert 'event.target.matches("[data-garden-select]")' in change_listener
    assert "resetSavedViewsForCurrentGarden();" in change_listener


def test_mobile_saved_view_actions_leave_navigation_and_quick_actions_operable() -> None:
    styles = (ROOT / "frontend/src/style.css").read_text(encoding="utf-8")

    assert ".app-shell:has(#saved-views-dropdown:not([hidden])) .mobile-fab" in styles
    assert "pointer-events: none;" in styles
    assert "bottom: calc(78px + env(safe-area-inset-bottom, 0px));" in styles


def test_saved_views_escape_closes_the_dropdown_and_restores_focus() -> None:
    saved_views = (ROOT / "frontend/src/features/savedViewsFeature.ts").read_text(encoding="utf-8")
    layout = (ROOT / "frontend/src/components/layout.ts").read_text(encoding="utf-8")
    journey = (ROOT / "scripts/e2e/journeys/gardenMapPlants.cjs").read_text(encoding="utf-8")

    assert 'aria-controls="saved-views-dropdown" aria-expanded="false"' in layout
    assert 'document.addEventListener("keydown", (event)' in saved_views
    assert 'if (event.key !== "Escape") return;' in saved_views
    assert "closeSavedViewsDropdown(true);" in saved_views
    assert 'trigger?.setAttribute("aria-expanded", "false");' in saved_views
    assert '.setAttribute("aria-expanded", "true")' in saved_views
    assert 'page.locator("#saved-views-dropdown:not([hidden])")' in journey
