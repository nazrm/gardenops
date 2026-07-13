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


def test_notification_preference_time_and_severity_controls_have_accessible_names() -> None:
    component = (ROOT / "frontend/src/components/notifications.ts").read_text(encoding="utf-8")

    assert 'quietStart.id = "notification-prefs-quiet-start";' in component
    assert 'quietEnd.id = "notification-prefs-quiet-end";' in component
    assert "quietStart.setAttribute(" in component
    assert "quietEnd.setAttribute(" in component
    assert "select.setAttribute(" in component
    assert '`${policyLabel(policy.key)}: ${t("notifications.prefs_min_severity")}`' in component


def test_viewers_keep_today_and_weather_navigation_without_write_affordances() -> None:
    attention = (ROOT / "frontend/src/components/attentionTodayPanel.ts").read_text(
        encoding="utf-8"
    )
    weather = (ROOT / "frontend/src/components/weather.ts").read_text(encoding="utf-8")
    app = (ROOT / "frontend/src/app.ts").read_text(encoding="utf-8")

    assert "canWrite?: () => boolean;" in attention
    assert "function isAttentionWriteAction" in attention
    assert 'button.dataset["attentionActionKind"] = action.kind;' in attention
    assert 'button.dataset["attentionActionTargetId"] = action.target_id;' in attention
    assert "if (options.canWrite?.() ?? true)" in attention
    assert "canWrite: () => canWriteInGarden," in app
    assert "function canWriteWeather" in weather
    assert "canWriteWeather()" in weather
    assert 'document.body.classList.toggle("garden-read-only", !canWriteInGarden);' in app


def test_today_preferences_preserve_untouched_rules_within_grouped_rows() -> None:
    attention = (ROOT / "frontend/src/components/attentionTodayPanel.ts").read_text(
        encoding="utf-8"
    )

    assert "const dirtyRuleRows = new Set<string>();" in attention
    assert "dirtyRuleRows.add(rowConfig.id);" in attention
    assert "if (!dirtyRuleRows.has(rowConfig.id)) return;" in attention
    assert "groupedRules.every((rule) => Boolean(rule.inbox))" in attention
    assert "groupedRules.every((rule) => Boolean(rule.digest))" in attention
