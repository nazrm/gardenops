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
const NOTIFICATION_POLL_INTERVAL = 60_000;
let pollTimerId: ReturnType<typeof setInterval> | null = null;
let notificationTriggerDelegationBound = false;
const NOTIFICATION_FEATURE_READY_ATTR =
  "data-notification-feature-ready";
const NOTIFICATION_TRIGGER_BOUND_ATTR =
  "data-notification-trigger-bound";

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
      onRead: async (n) => {
        await markNotificationReadApi(n.id);
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
        await dismissNotificationApi(n.id);
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
        closeNotificationPanel();
        if (n.target_type === "task") {
          ctx.navigateToSubMode("tasks");
          void ctx.loadTasks();
          if (n.target_id) {
            try {
              const task = await fetchTaskApi(n.target_id);
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
              await ctx.openIssueForm(issue);
            } catch {
              void ctx.loadIssues();
            }
          } else {
            void ctx.loadIssues();
          }
        } else if (n.target_type === "plant") {
          if (n.target_id) {
            ctx.focusPlantsInPlantsView([n.target_id]);
          } else {
            ctx.navigateToSubMode("plants");
          }
        } else if (n.target_type === "plot") {
          ctx.setActiveTab("map");
          if (n.target_id) {
            void ctx.selectPlot(n.target_id);
          }
        } else if (n.target_type === "weather_alert") {
          ctx.navigateToSubMode("care");
          void ctx.loadWeather();
        }
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
        await markAllNotificationsReadApi();
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
        try {
          const prefs = await fetchNotificationPreferencesApi();
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
    if (pollTimerId === null) {
      void loadNotificationCount();
      pollTimerId = setInterval(
        () => void loadNotificationCount(),
        NOTIFICATION_POLL_INTERVAL,
      );
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
      notificationPanelOpen = false;
      panel.hidden = true;
    }
  });

  void loadNotificationCount();
  pollTimerId = setInterval(
    () => void loadNotificationCount(),
    NOTIFICATION_POLL_INTERVAL,
  );
}

export async function loadNotificationCount(): Promise<void> {
  try {
    applyBadgeCounts(await fetchBadgeCountsApi({ force: true }));
  } catch (err) {
    // Stop polling on auth failure — the global onAuthExpired handler
    // already shows a "session expired" banner via checked() in api.ts.
    if (err instanceof ApiError && err.status === 401) {
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
  const panel = document.getElementById(
    "notification-panel",
  );
  if (!panel) return;
  notificationPanelOpen = !notificationPanelOpen;
  panel.hidden = !notificationPanelOpen;
  if (notificationPanelOpen) {
    notificationPanelView = "inbox";
    await loadNotifications();
  }
}

async function loadNotifications(): Promise<void> {
  try {
    const result = await fetchNotificationsApi({
      scope: notificationPanelView,
      limit: 30,
      offset: 0,
    });
    notificationItems = notificationsForView(
      result.notifications,
      notificationPanelView,
    );
    renderCurrentNotificationPanel();
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

function closeNotificationPanel(): void {
  notificationPanelOpen = false;
  const panel = document.getElementById(
    "notification-panel",
  );
  if (panel) panel.hidden = true;
}

async function showNotificationPreferences(): Promise<void> {
  const panel = document.getElementById(
    "notification-panel",
  );
  if (!panel) return;
  try {
    const prefs = await fetchNotificationPreferencesApi();
    renderNotificationPreferencesForm(
      panel,
      prefs,
      async (updated) => {
        try {
          await updateNotificationPreferencesApi(updated);
          ctx.showToast(
            t("notifications.prefs_saved"),
            "success",
          );
          notificationPanelView = "inbox";
          await loadNotificationCount();
          await loadNotifications();
        } catch (err) {
          ctx.showToast(
            getApiErrorMessage(err),
            "error",
          );
        }
      },
    );
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}
