from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_notification_panel_has_direct_keyboard_and_touch_dismissal() -> None:
    feature = (ROOT / "frontend/src/features/notificationsFeature.ts").read_text(encoding="utf-8")
    component = (ROOT / "frontend/src/components/notifications.ts").read_text(encoding="utf-8")
    layout = (ROOT / "frontend/src/components/layout.ts").read_text(encoding="utf-8")

    assert "onClose?: () => void;" in component
    assert 'closeBtn.setAttribute("aria-label", t("common.close") as string);' in component
    assert "onClose: () => closeNotificationPanel(true)," in feature
    assert 'event.key !== "Escape" || !notificationPanelOpen' in feature
    assert "notificationFocusReturnTarget?.focus();" in feature
    assert 'role="dialog" aria-label="${t("notifications.title")}"' in layout


def test_mobile_notification_panel_does_not_cover_primary_navigation() -> None:
    styles = (ROOT / "frontend/src/style.css").read_text(encoding="utf-8")

    mobile_panel = styles.split("@media (max-width: 960px)", 1)[1].split(
        "/* ── Weather dashboard", 1
    )[0]
    assert "bottom: calc(78px + env(safe-area-inset-bottom, 0px));" in mobile_panel
