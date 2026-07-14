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
    assert "Intl.DateTimeFormat().resolvedOptions().timeZone" in component
    assert "...(timeZone ? { timezone: timeZone } : {})" in component


def test_mobile_notification_preference_controls_have_touch_targets() -> None:
    styles = (ROOT / "frontend/src/style.css").read_text(encoding="utf-8")
    journey = (ROOT / "scripts/e2e/journeys/dailyAttentionWork.cjs").read_text(encoding="utf-8")

    mobile_panel = styles.split("@media (max-width: 960px)", 1)[1].split(
        "/* ── Weather dashboard", 1
    )[0]
    for control in ("button", "input", "select"):
        assert f".notification-prefs-form {control}" in mobile_panel
    assert "min-width: 44px;" in mobile_panel
    assert "min-height: 44px;" in mobile_panel
    assert 'prefs.locator("button:visible, select:visible, input:visible")' in journey


def test_notification_navigation_and_settings_are_native_keyboard_controls() -> None:
    feature = (ROOT / "frontend/src/features/notificationsFeature.ts").read_text(encoding="utf-8")
    component = (ROOT / "frontend/src/components/notifications.ts").read_text(encoding="utf-8")

    assert 'const content = document.createElement("button")' in component
    assert 'content.type = "button";' in component
    assert 'content.addEventListener("click", () => cbs.onNavigate(notification))' in component
    assert 'const form = document.createElement("form")' in component
    assert (
        'container.setAttribute("aria-labelledby", "notification-preferences-title")' in component
    )
    assert 'cancelBtn.className = "notification-prefs-cancel"' in component
    assert 'cancelBtn.addEventListener("click", onCancel)' in component
    assert 'saveBtn.type = "submit"' in component
    assert 'if (notificationPanelMode === "settings")' in feature
    assert "exitNotificationSettings();" in feature
    assert 'querySelector<HTMLElement>(".notification-settings-btn")' in feature


def test_notification_mutations_recover_from_api_failures_without_duplicate_actions() -> None:
    feature = (ROOT / "frontend/src/features/notificationsFeature.ts").read_text(encoding="utf-8")
    component = (ROOT / "frontend/src/components/notifications.ts").read_text(encoding="utf-8")

    dismiss = feature.split("onDismiss: async (n) => {", 1)[1].split("onNavigate: async", 1)[0]
    mark_all = feature.split("onMarkAllRead: async () => {", 1)[1].split("onOpenSettings:", 1)[0]

    assert "function beginNotificationMutation(" in feature
    assert "function finishNotificationMutation(" in feature
    assert "notificationMutationInFlight = null;" in feature
    assert "isMutationPending: notificationMutationIsInFlight," in feature
    assert 'ctx.showToast(getApiErrorMessage(err), "error")' in feature
    for action, api_call, state_update in (
        (
            dismiss,
            "await dismissNotificationApi(n.id);",
            "notificationItems = notificationItems.filter(",
        ),
        (mark_all, "await markAllNotificationsReadApi();", "notificationUnreadCount = 0;"),
    ):
        assert "const operation = beginNotificationMutation(request);" in action
        assert action.index(api_call) < action.index(state_update)
        assert "catch (err) {" in action
        assert "throw err;" in action
        assert "finally {" in action
        assert "finishNotificationMutation(operation);" in action

    assert "onDismiss: (notification: NotificationEvent) => void | Promise<void>;" in component
    assert "onMarkAllRead: () => void | Promise<void>;" in component
    assert "onActionError?: (error: unknown) => void;" in component
    assert "isMutationPending?: () => boolean;" in component
    assert "async function runNotificationAction(" in component
    assert "if (button.disabled) return;" in component
    assert "setNotificationActionPending(button, true);" in component
    assert "await action();" in component
    assert "onError?.(err);" in component
    assert "setNotificationActionPending(button, false);" in component
    assert component.count("void runNotificationAction(") == 2
    assert component.count("cbs.isMutationPending?.() ?? false") == 2


def test_notification_outside_click_uses_stable_event_path() -> None:
    feature = (ROOT / "frontend/src/features/notificationsFeature.ts").read_text(encoding="utf-8")

    assert "const eventPath = e.composedPath();" in feature
    assert "!eventPath.includes(panel)" in feature
    assert "!panel.contains(e.target as Node)" not in feature


def test_notification_view_switch_never_reuses_previous_view_items_after_failure() -> None:
    feature = (ROOT / "frontend/src/features/notificationsFeature.ts").read_text(encoding="utf-8")
    render = feature.split("function renderCurrentNotificationPanel", 1)[1].split(
        "export function initNotificationsFeature", 1
    )[0]
    load = feature.split("async function loadNotifications", 1)[1].split(
        "function closeNotificationPanel", 1
    )[0]

    assert 'let notificationItemsView: "inbox" | "log" | null = null;' in feature
    assert "notificationItemsView === notificationPanelView ? notificationItems : []" in render
    assert "notificationItemsView = view;" in load
    assert "notificationItems = [];" in load
    assert "renderCurrentNotificationPanel();" in load


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


def test_today_preferences_disable_digest_until_delivery_is_configured() -> None:
    attention = (ROOT / "frontend/src/components/attentionTodayPanel.ts").read_text(
        encoding="utf-8"
    )
    models = (ROOT / "frontend/src/core/models.ts").read_text(encoding="utf-8")

    assert "digest_delivery?:" in models
    assert "const digestConfigured = preferences.digest_delivery?.configured ?? true;" in attention
    assert 'surface === "digest" && !digestConfigured' in attention
    assert "enabled.disabled = true;" in attention
    assert 'toggle.setAttribute("aria-describedby", digestDescriptionId);' in attention
