import type { AppContext } from "../core/appContext";
import type {
  NotificationEvent,
  NotificationPreferences,
} from "../core/models";
import { t } from "../core/i18n";
import {
  ApiError,
  type BadgeCounts,
  fetchNotificationsApi,
  fetchBadgeCountsApi,
  fetchIssueApi,
  fetchTaskApi,
  markNotificationReadApi,
  markAllNotificationsReadApi,
  dismissNotificationApi,
  fetchNotificationPreferencesApi,
  getActiveGardenContext,
  updateNotificationPreferencesApi,
  getApiErrorMessage,
} from "../services/api";
import {
  renderNotificationPanel,
  renderNotificationPreferencesForm,
} from "../components/notifications";

let ctx: AppContext;
let notificationsInitialized = false;

let notificationItems: NotificationEvent[] = [];
let notificationUnreadCount = 0;
let notificationPanelOpen = false;
let notificationPanelView: "inbox" | "log" = "inbox";
let notificationPanelMode: "list" | "settings" = "list";
let notificationGardenId: number | null = null;
let notificationRequestVersion = 0;
let notificationCountLoadVersion = 0;
let notificationItemsLoadVersion = 0;
let notificationFocusReturnTarget: HTMLElement | null = null;
const NOTIFICATION_POLL_INTERVAL = 60_000;
let pollTimerId: ReturnType<typeof setInterval> | null = null;
let notificationTriggerDelegationBound = false;
const NOTIFICATION_FEATURE_READY_ATTR =
  "data-notification-feature-ready";
const NOTIFICATION_TRIGGER_BOUND_ATTR =
  "data-notification-trigger-bound";
const NOTIFICATION_TRIGGER_SELECTOR =
  "#notification-bell, #mobile-notification-btn";

interface NotificationRequestContext {
  gardenId: number;
  version: number;
}

interface NotificationMutationOperation {
  request: NotificationRequestContext;
}

let notificationMutationInFlight: NotificationMutationOperation | null = null;

function notificationRequestContext(): NotificationRequestContext | null {
  const gardenId = getActiveGardenContext();
  if (gardenId === null || notificationGardenId !== gardenId) return null;
  return { gardenId, version: notificationRequestVersion };
}

function isCurrentNotificationRequest(
  request: NotificationRequestContext,
): boolean {
  return (
    request.version === notificationRequestVersion
    && request.gardenId === notificationGardenId
    && request.gardenId === getActiveGardenContext()
  );
}

function notificationMutationIsInFlight(): boolean {
  const operation = notificationMutationInFlight;
  return operation !== null && isCurrentNotificationRequest(operation.request);
}

function beginNotificationMutation(
  request: NotificationRequestContext,
): NotificationMutationOperation | null {
  if (notificationMutationIsInFlight()) return null;
  const operation = { request };
  notificationMutationInFlight = operation;
  renderCurrentNotificationPanel();
  return operation;
}

function finishNotificationMutation(
  operation: NotificationMutationOperation,
): void {
  if (notificationMutationInFlight === operation) {
    notificationMutationInFlight = null;
  }
  if (isCurrentNotificationRequest(operation.request)) {
    renderCurrentNotificationPanel();
  }
}

function clearNotificationState(): void {
  notificationItems = [];
  notificationUnreadCount = 0;
  document.getElementById("notification-panel")?.replaceChildren();
  updateNotificationBadge();
  updateTabBadge("activity", 0);
  updateTabBadge("insights", 0);
}

export function resetNotificationsForCurrentGarden(): void {
  notificationGardenId = getActiveGardenContext();
  notificationRequestVersion += 1;
  notificationCountLoadVersion += 1;
  notificationItemsLoadVersion += 1;
  notificationMutationInFlight = null;
  notificationPanelView = "inbox";
  notificationPanelMode = "list";
  closeNotificationPanel();
  clearNotificationState();

  const request = notificationRequestContext();
  if (notificationsInitialized && request) {
    void loadNotificationCount(request);
  }
}

export function syncNotificationsForCurrentGarden(): void {
  if (notificationGardenId === getActiveGardenContext()) return;
  resetNotificationsForCurrentGarden();
}

function ensureNotificationPoller(): void {
  if (pollTimerId !== null) return;
  pollTimerId = setInterval(
    () => void loadNotificationCount(),
    NOTIFICATION_POLL_INTERVAL,
  );
}

function notificationsForView(
  notifications: NotificationEvent[],
  view: "inbox" | "log",
): NotificationEvent[] {
  if (view === "log") return notifications;
  const now = Date.now();
  return notifications.filter(
    (notification) =>
      !notification.dismissed
      && !notification.clear_reason
      && (
        notification.expires_at_ms === null
        || notification.expires_at_ms >= now
      ),
  );
}

function notificationPanelElement(): HTMLElement | null {
  const panel = document.getElementById("notification-panel");
  return panel instanceof HTMLElement ? panel : null;
}

function updateNotificationTriggerAccessibility(): void {
  const label = t(
    notificationPanelOpen
      ? "notifications.close_panel"
      : "notifications.open_panel",
  ) as string;
  document
    .querySelectorAll<HTMLElement>(NOTIFICATION_TRIGGER_SELECTOR)
    .forEach((trigger) => {
      trigger.setAttribute(NOTIFICATION_TRIGGER_BOUND_ATTR, "true");
      trigger.setAttribute("aria-controls", "notification-panel");
      trigger.setAttribute("aria-haspopup", "dialog");
      trigger.setAttribute("aria-expanded", String(notificationPanelOpen));
      trigger.setAttribute("aria-label", label);
    });
}

function setNotificationPanelHidden(
  panel: HTMLElement,
  hidden: boolean,
): void {
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "true");
  panel.hidden = hidden;
  panel.setAttribute("aria-hidden", hidden ? "true" : "false");
  if (hidden) {
    panel.setAttribute("inert", "");
    panel.removeAttribute("tabindex");
    panel.removeAttribute("aria-labelledby");
    panel.setAttribute("aria-label", t("notifications.title") as string);
  } else {
    panel.removeAttribute("inert");
    panel.tabIndex = -1;
  }
  updateNotificationTriggerAccessibility();
}

function focusableNotificationPanelElements(
  panel: HTMLElement,
): HTMLElement[] {
  const candidates = panel.querySelectorAll<HTMLElement>(
    [
      "button:not([disabled])",
      "a[href]",
      "input:not([disabled])",
      "select:not([disabled])",
      "textarea:not([disabled])",
      "[tabindex]:not([tabindex='-1'])",
    ].join(","),
  );
  return Array.from(candidates).filter((element) => {
    const style = window.getComputedStyle(element);
    return style.display !== "none" && style.visibility !== "hidden";
  });
}

function focusNotificationPanel(): void {
  const panel = notificationPanelElement();
  if (!panel || !notificationPanelOpen) return;
  const first = focusableNotificationPanelElements(panel)[0];
  (first ?? panel).focus();
}

function restoreNotificationPanelFocus(): void {
  const target = notificationFocusReturnTarget;
  notificationFocusReturnTarget = null;
  if (!target?.isConnected || target.matches(":disabled")) return;

  if (target.id === "mobile-notification-btn") {
    const mobileUtilitySheet = document.getElementById("mobile-utility-sheet");
    const mobileUtilityTrigger = document.getElementById("mobile-utility-btn");
    if (
      mobileUtilitySheet instanceof HTMLElement
      && !mobileUtilitySheet.classList.contains("mobile-utility-sheet--open")
      && mobileUtilityTrigger instanceof HTMLElement
    ) {
      mobileUtilityTrigger.click();
    }
  }
  window.requestAnimationFrame(() => target.focus());
}

function trapNotificationPanelFocus(event: KeyboardEvent): void {
  if (!notificationPanelOpen) return;
  const panel = notificationPanelElement();
  if (!panel) return;
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    if (notificationPanelMode === "settings") {
      exitNotificationSettings();
      return;
    }
    closeNotificationPanel(true);
    return;
  }
  if (event.key !== "Tab") return;

  const focusable = focusableNotificationPanelElements(panel);
  if (focusable.length === 0) {
    event.preventDefault();
    panel.focus();
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (!first || !last) return;
  const active = document.activeElement;
  if (!(active instanceof HTMLElement) || !panel.contains(active) || active === panel) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus();
    return;
  }
  if (event.shiftKey && active === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
  }
}

function exitNotificationSettings(): void {
  if (!notificationPanelOpen) return;
  notificationPanelMode = "list";
  renderCurrentNotificationPanel();
  window.requestAnimationFrame(() => {
    notificationPanelElement()
      ?.querySelector<HTMLElement>(".notification-settings-btn")
      ?.focus();
  });
}

function markNotificationTrigger(id: string): void {
  const trigger = document.getElementById(id);
  if (!(trigger instanceof HTMLElement)) return;
  updateNotificationTriggerAccessibility();
}

function isNotificationTriggerTarget(
  target: EventTarget | null,
): boolean {
  if (!(target instanceof Element)) return false;
  return Boolean(
    target.closest(NOTIFICATION_TRIGGER_SELECTOR),
  );
}

function bindNotificationTriggers(): void {
  if (!notificationTriggerDelegationBound) {
    document.addEventListener("click", (event) => {
      if (!isNotificationTriggerTarget(event.target)) return;
      event.stopPropagation();
      const trigger = (event.target as Element).closest<HTMLElement>(
        "#notification-bell, #mobile-notification-btn",
      );
      if (trigger) notificationFocusReturnTarget = trigger;
      void toggleNotificationPanel();
    });
    notificationTriggerDelegationBound = true;
  }
  document.documentElement.setAttribute(
    NOTIFICATION_FEATURE_READY_ATTR,
    "true",
  );
  const panel = notificationPanelElement();
  if (panel) setNotificationPanelHidden(panel, !notificationPanelOpen);
  markNotificationTrigger("notification-bell");
  markNotificationTrigger("mobile-notification-btn");
}

function fullNotificationPreferencesUpdate(
  prefs: NotificationPreferences,
  ruleKey: string,
): Omit<NotificationPreferences, "policy"> | null {
  const rule = prefs.notification_rules[ruleKey];
  if (!rule) return null;
  return {
    in_app_enabled: prefs.in_app_enabled,
    email_enabled: prefs.email_enabled,
    email_address: prefs.email_address,
    digest_frequency: prefs.digest_frequency,
    quiet_hours_json: { ...prefs.quiet_hours_json },
    task_due_enabled: prefs.task_due_enabled,
    task_overdue_enabled: prefs.task_overdue_enabled,
    notification_rules: {
      ...prefs.notification_rules,
      [ruleKey]: {
        ...rule,
        in_app_enabled: false,
      },
    },
  };
}

function renderCurrentNotificationPanel(): void {
  const panel = notificationPanelElement();
  if (!panel || !notificationPanelOpen || notificationPanelMode !== "list") return;
  const hadPanelFocus = document.activeElement instanceof HTMLElement
    && panel.contains(document.activeElement);
  const focusedTabId = document.activeElement instanceof HTMLElement
    && document.activeElement.getAttribute("role") === "tab"
    ? document.activeElement.id
    : null;
  renderNotificationPanel(
    panel,
    notificationItems,
    notificationUnreadCount,
    {
      onClose: () => closeNotificationPanel(true),
      onActionError: (err) =>
        ctx.showToast(getApiErrorMessage(err), "error"),
      isMutationPending: notificationMutationIsInFlight,
      onRead: async (n) => {
        const request = notificationRequestContext();
        if (!request) return;
        await markNotificationReadApi(n.id);
        if (!isCurrentNotificationRequest(request)) return;
        if (!n.read_at_ms) {
          n.read_at_ms = Date.now();
          notificationUnreadCount = Math.max(
            0,
            notificationUnreadCount - 1,
          );
          updateNotificationBadge();
          renderCurrentNotificationPanel();
        }
      },
      onDismiss: async (n) => {
        const request = notificationRequestContext();
        if (!request) return;
        const operation = beginNotificationMutation(request);
        if (!operation) return;
        try {
          await dismissNotificationApi(n.id);
          if (!isCurrentNotificationRequest(request)) return;
          notificationItems = notificationItems.filter(
            (item) => item.id !== n.id,
          );
          if (!n.read_at_ms) {
            notificationUnreadCount = Math.max(
              0,
              notificationUnreadCount - 1,
            );
            updateNotificationBadge();
          }
        } catch (err) {
          if (!isCurrentNotificationRequest(request)) return;
          throw err;
        } finally {
          finishNotificationMutation(operation);
        }
      },
      onNavigate: async (n) => {
        const request = notificationRequestContext();
        if (!request) return;
        closeNotificationPanel();
        if (n.target_type === "task") {
          ctx.navigateToSubMode("tasks");
          void ctx.loadTasks();
          if (n.target_id) {
            try {
              const task = await fetchTaskApi(n.target_id);
              if (!isCurrentNotificationRequest(request)) return;
              await ctx.openTaskForm(task);
            } catch {
              // fall back to the tasks list already opened above
            }
          }
        } else if (n.target_type === "issue") {
          ctx.navigateToSubMode("issues");
          if (n.target_id) {
            try {
              const issue = await fetchIssueApi(n.target_id);
              if (!isCurrentNotificationRequest(request)) return;
              await ctx.openIssueForm(issue);
            } catch {
              void ctx.loadIssues();
            }
          } else {
            void ctx.loadIssues();
          }
        } else if (n.target_type === "plant") {
          if (!isCurrentNotificationRequest(request)) return;
          if (n.target_id) {
            ctx.focusPlantsInPlantsView([n.target_id]);
          } else {
            ctx.navigateToSubMode("plants");
          }
        } else if (n.target_type === "plot") {
          if (!isCurrentNotificationRequest(request)) return;
          ctx.setActiveTab("map");
          if (n.target_id) {
            void ctx.selectPlot(n.target_id);
          }
        } else if (n.target_type === "weather_alert") {
          if (!isCurrentNotificationRequest(request)) return;
          ctx.navigateToSubMode("care");
          void ctx.loadWeather();
        }
        if (!isCurrentNotificationRequest(request)) return;
        if (!n.read_at_ms) {
          n.read_at_ms = Date.now();
          notificationUnreadCount = Math.max(
            0,
            notificationUnreadCount - 1,
          );
          updateNotificationBadge();
          void markNotificationReadApi(n.id).catch(() => {
            // The next poll/open will reconcile if the read mark fails.
          });
        }
      },
      onMarkAllRead: async () => {
        const request = notificationRequestContext();
        if (!request) return;
        const operation = beginNotificationMutation(request);
        if (!operation) return;
        try {
          await markAllNotificationsReadApi();
          if (!isCurrentNotificationRequest(request)) return;
          let unreadChanged = false;
          for (const notification of notificationItems) {
            if (notification.read_at_ms) continue;
            notification.read_at_ms = Date.now();
            unreadChanged = true;
          }
          if (unreadChanged) {
            notificationUnreadCount = 0;
            updateNotificationBadge();
          }
        } catch (err) {
          if (!isCurrentNotificationRequest(request)) return;
          throw err;
        } finally {
          finishNotificationMutation(operation);
        }
      },
      onOpenSettings: () =>
        void showNotificationPreferences(),
      onViewChange: (view) => {
        notificationPanelView = view;
        renderCurrentNotificationPanel();
        void loadNotifications();
      },
      onMuteType: async (n) => {
        const request = notificationRequestContext();
        if (!request) return;
        try {
          const prefs = await fetchNotificationPreferencesApi();
          if (!isCurrentNotificationRequest(request)) return;
          const key = n.notification_subtype
            ? `${n.notification_type}:${n.notification_subtype}`
            : n.notification_type;
          const updated = fullNotificationPreferencesUpdate(prefs, key);
          if (!updated) return;
          await updateNotificationPreferencesApi(updated);
          if (!isCurrentNotificationRequest(request)) return;
          notificationItems = notificationItems.filter(
            (item) =>
              (item.notification_subtype
                ? `${item.notification_type}:${item.notification_subtype}`
                : item.notification_type) !== key,
          );
          await ctx.refreshBadgeCounts();
          renderCurrentNotificationPanel();
          ctx.showToast(
            t("notifications.prefs_saved"),
            "success",
          );
        } catch (err) {
          if (!isCurrentNotificationRequest(request)) return;
          ctx.showToast(getApiErrorMessage(err), "error");
        }
      },
      view: notificationPanelView,
    },
  );
  if (hadPanelFocus) {
    window.requestAnimationFrame(() => {
      if (!notificationPanelOpen || notificationPanelMode !== "list") return;
      const selectedTabId = focusedTabId
        ? `notification-tab-${notificationPanelView}`
        : null;
      const selectedTab = selectedTabId
        ? document.getElementById(selectedTabId)
        : null;
      if (selectedTab instanceof HTMLElement) {
        selectedTab.focus();
      } else if (!panel.contains(document.activeElement)) {
        focusNotificationPanel();
      }
    });
  }
}

export function initNotificationsFeature(
  appCtx: AppContext,
): void {
  ctx = appCtx;
  bindNotificationTriggers();
  if (notificationsInitialized) {
    ensureNotificationPoller();
    if (notificationGardenId !== getActiveGardenContext()) {
      resetNotificationsForCurrentGarden();
    }
    return;
  }
  notificationsInitialized = true;

  document.addEventListener("click", (e) => {
    if (!notificationPanelOpen) return;
    const eventPath = e.composedPath();
    const panel = document.getElementById(
      "notification-panel",
    );
    const bell = document.getElementById(
      "notification-bell",
    );
    const wrapper = bell?.closest(
      ".notification-bell-wrapper",
    );
    const mobileButton = document.getElementById(
      "mobile-notification-btn",
    );
    if (
      panel &&
      !eventPath.includes(panel) &&
      !(wrapper && eventPath.includes(wrapper)) &&
      !(mobileButton && eventPath.includes(mobileButton))
    ) {
      closeNotificationPanel();
    }
  });
  document.addEventListener("keydown", trapNotificationPanelFocus, true);

  resetNotificationsForCurrentGarden();
  ensureNotificationPoller();
}

export async function loadNotificationCount(
  request: NotificationRequestContext | null = notificationRequestContext(),
): Promise<void> {
  if (!request) return;
  const loadVersion = ++notificationCountLoadVersion;
  try {
    const counts = await fetchBadgeCountsApi({ force: true });
    if (
      loadVersion !== notificationCountLoadVersion
      || !isCurrentNotificationRequest(request)
    ) {
      return;
    }
    applyBadgeCounts(counts);
  } catch (err) {
    // Stop polling on auth failure — the global onAuthExpired handler
    // already shows a "session expired" banner via checked() in api.ts.
    if (
      err instanceof ApiError
      && err.status === 401
      && isCurrentNotificationRequest(request)
    ) {
      if (pollTimerId !== null) {
        clearInterval(pollTimerId);
        pollTimerId = null;
      }
      return;
    }
    // Silently ignore other errors - non-critical
  }
}

export async function refreshNotificationsForCurrentGarden(): Promise<void> {
  syncNotificationsForCurrentGarden();
  const request = notificationRequestContext();
  if (!request) return;
  await loadNotificationCount(request);
  if (!isCurrentNotificationRequest(request) || !notificationPanelOpen) return;
  await loadNotifications();
}

function applyBadgeCounts(counts: BadgeCounts): void {
  notificationUnreadCount = counts.unread_notifications;
  updateNotificationBadge();
  updateTabBadge(
    "activity",
    counts.overdue_tasks + counts.open_issues,
  );
  updateTabBadge("insights", counts.active_alerts);
}

function updateTabBadge(
  tab: string,
  count: number,
): void {
  const badge = document.getElementById(
    `tab-badge-${tab}`,
  );
  if (!badge) return;
  if (count > 0) {
    badge.textContent =
      count > 99 ? "99+" : String(count);
    badge.hidden = false;
  } else {
    badge.hidden = true;
  }
}

function updateNotificationBadge(): void {
  const desktopBadge = document.getElementById(
    "notification-badge",
  );
  const mobileBadge = document.getElementById(
    "mobile-notification-badge",
  );
  for (const badge of [desktopBadge, mobileBadge]) {
    if (!badge) continue;
    if (notificationUnreadCount > 0) {
      badge.textContent =
        notificationUnreadCount > 99
          ? "99+"
          : String(notificationUnreadCount);
      badge.hidden = false;
    } else {
      badge.hidden = true;
    }
  }
}

async function toggleNotificationPanel(): Promise<void> {
  syncNotificationsForCurrentGarden();
  const panel = notificationPanelElement();
  if (!panel) return;
  if (notificationPanelOpen) {
    closeNotificationPanel(true);
    return;
  }

  notificationPanelOpen = true;
  notificationPanelView = "inbox";
  notificationPanelMode = "list";
  setNotificationPanelHidden(panel, false);
  renderCurrentNotificationPanel();
  focusNotificationPanel();
  await loadNotifications();
  if (notificationPanelOpen) {
    window.requestAnimationFrame(focusNotificationPanel);
  }
}

async function loadNotifications(): Promise<void> {
  const request = notificationRequestContext();
  if (!request) return;
  const view = notificationPanelView;
  const loadVersion = ++notificationItemsLoadVersion;
  try {
    const result = await fetchNotificationsApi({
      scope: view,
      limit: 30,
      offset: 0,
    });
    if (
      loadVersion !== notificationItemsLoadVersion
      || notificationPanelView !== view
      || !isCurrentNotificationRequest(request)
    ) {
      return;
    }
    notificationItems = notificationsForView(
      result.notifications,
      view,
    );
    renderCurrentNotificationPanel();
  } catch (err) {
    if (!isCurrentNotificationRequest(request)) return;
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

function closeNotificationPanel(restoreFocus = false): void {
  notificationPanelOpen = false;
  notificationPanelMode = "list";
  const panel = notificationPanelElement();
  if (panel) setNotificationPanelHidden(panel, true);
  if (restoreFocus) restoreNotificationPanelFocus();
  else notificationFocusReturnTarget = null;
}

async function showNotificationPreferences(): Promise<void> {
  const panel = document.getElementById(
    "notification-panel",
  );
  if (!panel) return;
  const request = notificationRequestContext();
  if (!request) return;
  notificationPanelMode = "settings";
  notificationItemsLoadVersion += 1;
  try {
    const prefs = await fetchNotificationPreferencesApi();
    if (
      !isCurrentNotificationRequest(request)
      || !notificationPanelOpen
      || notificationPanelMode !== "settings"
    ) return;
    renderNotificationPreferencesForm(
      panel,
      prefs,
      async (updated) => {
        try {
          await updateNotificationPreferencesApi(updated);
          if (!isCurrentNotificationRequest(request)) return;
          ctx.showToast(
            t("notifications.prefs_saved"),
            "success",
          );
          notificationPanelView = "inbox";
          notificationPanelMode = "list";
          await ctx.refreshBadgeCounts();
          await loadNotifications();
          window.requestAnimationFrame(() => {
            notificationPanelElement()
              ?.querySelector<HTMLElement>(".notification-settings-btn")
              ?.focus();
          });
        } catch (err) {
          if (!isCurrentNotificationRequest(request)) return;
          ctx.showToast(
            getApiErrorMessage(err),
            "error",
          );
        }
      },
      exitNotificationSettings,
    );
    focusNotificationPanel();
  } catch (err) {
    if (!isCurrentNotificationRequest(request)) return;
    notificationPanelMode = "list";
    renderCurrentNotificationPanel();
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}
