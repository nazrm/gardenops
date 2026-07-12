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
