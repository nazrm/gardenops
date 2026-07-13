import type { AppContext } from "../core/appContext";
import type { NotificationEvent } from "../core/models";
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

interface NotificationRequestContext {
  gardenId: number;
  version: number;
}

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
  notificationPanelView = "inbox";
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

function markNotificationTrigger(id: string): void {
  const trigger = document.getElementById(id);
  if (!(trigger instanceof HTMLElement)) return;
  trigger.setAttribute(NOTIFICATION_TRIGGER_BOUND_ATTR, "true");
}

function isNotificationTriggerTarget(
  target: EventTarget | null,
): boolean {
  if (!(target instanceof Element)) return false;
  return Boolean(
    target.closest("#notification-bell, #mobile-notification-btn"),
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
  markNotificationTrigger("notification-bell");
  markNotificationTrigger("mobile-notification-btn");
}

function renderCurrentNotificationPanel(): void {
  const panel = document.getElementById(
    "notification-panel",
  );
  if (!panel || !notificationPanelOpen) return;
  renderNotificationPanel(
    panel,
    notificationItems,
    notificationUnreadCount,
    {
      onClose: () => closeNotificationPanel(true),
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
        renderCurrentNotificationPanel();
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
        renderCurrentNotificationPanel();
      },
      onOpenSettings: () =>
        void showNotificationPreferences(),
      onViewChange: (view) => {
        notificationPanelView = view;
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
          const rule = prefs.notification_rules[key];
          if (!rule) return;
          await updateNotificationPreferencesApi({
            notification_rules: {
              ...prefs.notification_rules,
              [key]: {
                ...rule,
                in_app_enabled: false,
              },
            },
          });
          if (!isCurrentNotificationRequest(request)) return;
          notificationItems = notificationItems.filter(
            (item) =>
              (item.notification_subtype
                ? `${item.notification_type}:${item.notification_subtype}`
                : item.notification_type) !== key,
          );
          await loadNotificationCount();
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
      !panel.contains(e.target as Node) &&
      !(wrapper?.contains(e.target as Node) ?? false) &&
      !(mobileButton?.contains(e.target as Node) ?? false)
    ) {
      closeNotificationPanel();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !notificationPanelOpen) return;
    event.preventDefault();
    closeNotificationPanel(true);
  });

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
  const panel = document.getElementById(
    "notification-panel",
  );
  if (!panel) return;
  notificationPanelOpen = !notificationPanelOpen;
  panel.hidden = !notificationPanelOpen;
  if (notificationPanelOpen) {
    panel.tabIndex = -1;
    panel.focus();
    notificationPanelView = "inbox";
    await loadNotifications();
  } else {
    panel.removeAttribute("tabindex");
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
  const panel = document.getElementById(
    "notification-panel",
  );
  if (panel) {
    panel.hidden = true;
    panel.removeAttribute("tabindex");
  }
  if (restoreFocus) notificationFocusReturnTarget?.focus();
}

async function showNotificationPreferences(): Promise<void> {
  const panel = document.getElementById(
    "notification-panel",
  );
  if (!panel) return;
  const request = notificationRequestContext();
  if (!request) return;
  try {
    const prefs = await fetchNotificationPreferencesApi();
    if (!isCurrentNotificationRequest(request)) return;
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
          await loadNotificationCount();
          await loadNotifications();
        } catch (err) {
          if (!isCurrentNotificationRequest(request)) return;
          ctx.showToast(
            getApiErrorMessage(err),
            "error",
          );
        }
      },
    );
  } catch (err) {
    if (!isCurrentNotificationRequest(request)) return;
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}
