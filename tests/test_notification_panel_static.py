from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_notification_panel_has_direct_keyboard_and_touch_dismissal() -> None:
    feature = (ROOT / "frontend/src/features/notificationsFeature.ts").read_text(encoding="utf-8")
    component = (ROOT / "frontend/src/components/notifications.ts").read_text(encoding="utf-8")
    layout = (ROOT / "frontend/src/components/layout.ts").read_text(encoding="utf-8")

    assert "onClose?: () => void;" in component
    assert 'closeBtn.setAttribute("aria-label", t("common.close") as string);' in component
    assert "onClose: () => closeNotificationPanel(true)," in feature
    assert "if (!notificationPanelOpen) return;" in feature
    assert 'if (event.key === "Escape")' in feature
    assert "closeNotificationPanel(true);" in feature
    assert "function restoreNotificationPanelFocus(): void" in feature
    assert "if (restoreFocus) restoreNotificationPanelFocus();" in feature
    assert "window.requestAnimationFrame(() => target.focus());" in feature
    assert 'role="dialog" aria-label="${t("notifications.title")}"' in layout


def test_mobile_notification_panel_does_not_cover_primary_navigation() -> None:
    styles = (ROOT / "frontend/src/style.css").read_text(encoding="utf-8")

    mobile_panel = styles.split("@media (max-width: 960px)", 1)[1].split(
        "/* ── Weather dashboard", 1
    )[0]
    assert "bottom: calc(78px + env(safe-area-inset-bottom, 0px));" in mobile_panel


def test_preference_changes_refresh_attention_and_notification_surfaces() -> None:
    feature = (ROOT / "frontend/src/features/notificationsFeature.ts").read_text(encoding="utf-8")
    app = (ROOT / "frontend/src/app.ts").read_text(encoding="utf-8")

    assert feature.count("await ctx.refreshBadgeCounts();") >= 2
    attention_options = app.split("attentionTodayPanel = initAttentionTodayPanel", 1)[1].split(
        "onPrimaryAction:", 1
    )[0]
    assert "await refreshNotificationsForCurrentGarden();" in attention_options
