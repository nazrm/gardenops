/**
 * Notification panel and preferences form — pure render functions.
 */

import type {
  NotificationEvent,
  NotificationPreferences,
  NotificationRulePreference,
} from "../core/models";
import { t } from "../core/i18n";

export interface NotificationListCallbacks {
  onClose?: () => void;
  onRead: (notification: NotificationEvent) => void;
  onDismiss: (notification: NotificationEvent) => void;
  onNavigate: (notification: NotificationEvent) => void;
  onMarkAllRead: () => void;
  onOpenSettings?: () => void;
  onViewChange?: (view: "inbox" | "log") => void;
  onMuteType?: (notification: NotificationEvent) => void;
  view?: "inbox" | "log";
}

const TASK_NOTIF_KEYS: Record<
  string,
  { title: string; body: string; titleNamed: string; bodyNamed: string }
> = {
  task_due: {
    title: "notifications.task_due_title",
    body: "notifications.task_due_body",
    titleNamed: "notifications.task_due_title_named",
    bodyNamed: "notifications.task_due_body_named",
  },
  task_overdue: {
    title: "notifications.task_overdue_title",
    body: "notifications.task_overdue_body",
    titleNamed: "notifications.task_overdue_title_named",
    bodyNamed: "notifications.task_overdue_body_named",
  },
  task_upcoming: {
    title: "notifications.task_upcoming_title",
    body: "notifications.task_upcoming_body",
    titleNamed: "notifications.task_upcoming_title_named",
    bodyNamed: "notifications.task_upcoming_body_named",
  },
};

function formatNotificationPlants(
  metadata: NotificationEvent["metadata"],
): string {
  if (!metadata) return "";
  const rawPlants = metadata["plants"];
  const plants = Array.isArray(rawPlants) ? (rawPlants as string[]) : [];
  const rawCount = metadata["plant_count"];
  const count =
    typeof rawCount === "number"
      ? rawCount
      : plants.length;
  if (count > 3) {
    return t("notifications.plant_count", { count }) as string;
  }
  return plants.join(", ");
}

function localizeNotification(
  n: NotificationEvent,
): { title: string; body: string } {
  const keys = TASK_NOTIF_KEYS[n.notification_type];
  if (!keys) return { title: n.title, body: n.body };

  const m = n.metadata;
  const taskTitle = m ? m["task_title"] : null;
  if (m && typeof taskTitle === "string" && taskTitle) {
    const plants = formatNotificationPlants(m);
    const rawDue = m["due_on"];
    const due = typeof rawDue === "string" ? rawDue : "";
    return {
      title: t(keys.titleNamed, { title: taskTitle }) as string,
      body: t(keys.bodyNamed, {
        title: taskTitle,
        plants,
        due,
      }) as string,
    };
  }
  return {
    title: t(keys.title) as string,
    body: t(keys.body) as string,
  };
}

const TYPE_ICONS: Record<string, string> = {
  task_due: "\u23F0",
  task_overdue: "\uD83D\uDD34",
  task_upcoming: "\u{1F4C5}",
  task_generated: "\u2728",
  issue_created: "\u26A0\uFE0F",
  weather_alert: "\uD83C\uDF26\uFE0F",
  system: "\u2139\uFE0F",
};

const POLICY_LABEL_KEYS: Record<string, string> = {
  task_due: "notifications.policy_task_due",
  task_overdue: "notifications.policy_task_overdue",
  task_upcoming: "notifications.policy_task_upcoming",
  task_generated: "notifications.policy_task_generated",
  issue_created: "notifications.policy_issue_created",
  "weather_alert:frost_warning": "notifications.policy_weather_frost",
  "weather_alert:heat_wave": "notifications.policy_weather_heat",
  "weather_alert:dry_spell": "notifications.policy_weather_dry",
  "weather_alert:rain_surplus": "notifications.policy_weather_rain",
  system: "notifications.policy_system",
};

const POLICY_GROUP_KEYS: Record<string, string> = {
  tasks: "notifications.group_tasks",
  weather: "notifications.group_weather",
  issues: "notifications.group_issues",
  system: "notifications.group_system",
};

function policyLabel(key: string): string {
  return (t(POLICY_LABEL_KEYS[key] ?? `notifications.type_${key}`) as string) || key;
}

function timeAgo(timestampMs: number): string {
  const minutes = (Date.now() - timestampMs) / 60_000;
  return t("notifications.time_ago", { minutes }) as string;
}

function statusLabel(notification: NotificationEvent): string | null {
  if (notification.clear_reason) {
    return t(`notifications.clear_${notification.clear_reason}`) as string;
  }
  if (notification.dismissed) return t("notifications.clear_manual_dismiss") as string;
  if (notification.read_at_ms) return t("notifications.status_read") as string;
  return null;
}

function notificationRuleKey(notification: NotificationEvent): string {
  return notification.notification_subtype
    ? `${notification.notification_type}:${notification.notification_subtype}`
    : notification.notification_type;
}

export function createNotificationItem(
  notification: NotificationEvent,
  cbs: NotificationListCallbacks,
): HTMLElement {
  const item = document.createElement("div");
  item.className = `notification-item${notification.read_at_ms ? "" : " unread"}`;

  const icon = document.createElement("span");
  icon.className = "notification-item-icon";
  icon.textContent = TYPE_ICONS[notification.notification_type] ?? "\u2139\uFE0F";

  const content = document.createElement("div");
  content.className = "notification-item-content";

  const localized = localizeNotification(notification);

  const titleEl = document.createElement("div");
  titleEl.className = "notification-item-title";
  titleEl.textContent = localized.title;

  const bodyEl = document.createElement("div");
  bodyEl.className = "notification-item-body";
  bodyEl.textContent = localized.body;

  const timeEl = document.createElement("div");
  timeEl.className = "notification-item-time";
  timeEl.textContent = timeAgo(notification.created_at_ms);

  const metaRow = document.createElement("div");
  metaRow.className = "notification-item-meta";
  metaRow.append(timeEl);
  const status = statusLabel(notification);
  if (status) {
    const statusEl = document.createElement("span");
    statusEl.className = "notification-item-status";
    statusEl.textContent = status;
    metaRow.append(statusEl);
  }

  content.append(titleEl, bodyEl, metaRow);

  const dismissBtn = document.createElement("button");
  dismissBtn.className = "notification-item-dismiss";
  dismissBtn.type = "button";
  dismissBtn.textContent = "\u00D7";
  dismissBtn.title = t("notifications.dismiss") as string;
  dismissBtn.setAttribute("aria-label", t("notifications.dismiss") as string);
  dismissBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    cbs.onDismiss(notification);
  });

  item.append(icon, content);
  if (!notification.read_at_ms && !notification.clear_reason && !notification.dismissed) {
    const dot = document.createElement("span");
    dot.className = "notification-unread-dot";
    item.append(dot);
  }
  const actions = document.createElement("div");
  actions.className = "notification-item-actions";
  if (cbs.view !== "log" && cbs.onMuteType) {
    const muteBtn = document.createElement("button");
    muteBtn.className = "notification-item-mute";
    muteBtn.type = "button";
    muteBtn.textContent = t("notifications.mute_type") as string;
    const ruleLabel = policyLabel(notificationRuleKey(notification));
    muteBtn.title = ruleLabel;
    muteBtn.setAttribute(
      "aria-label",
      t("notifications.mute_type_label", { type: ruleLabel }) as string,
    );
    muteBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      cbs.onMuteType!(notification);
    });
    actions.append(muteBtn);
  }
  if (cbs.view !== "log") {
    actions.append(dismissBtn);
  }
  item.append(actions);

  item.addEventListener("click", () => {
    cbs.onNavigate(notification);
  });

  return item;
}

export function renderNotificationPanel(
  container: HTMLElement,
  notifications: NotificationEvent[],
  unreadCount: number,
  cbs: NotificationListCallbacks,
): void {
  container.replaceChildren();
  container.setAttribute("role", "dialog");
  container.setAttribute("aria-modal", "true");
  container.setAttribute("aria-labelledby", "notification-panel-title");
  container.removeAttribute("aria-label");
  const activeView = cbs.view ?? "inbox";

  const header = document.createElement("div");
  header.className = "notification-panel-header";

  const title = document.createElement("span");
  title.id = "notification-panel-title";
  title.className = "notification-panel-title";
  title.textContent = t("notifications.title") as string;

  const headerActions = document.createElement("div");
  headerActions.style.display = "flex";
  headerActions.style.alignItems = "center";
  headerActions.style.gap = "var(--sp-2)";

  if (activeView === "inbox" && unreadCount > 0) {
    const markAllBtn = document.createElement("button");
    markAllBtn.className = "notification-mark-all-btn";
    markAllBtn.type = "button";
    markAllBtn.textContent = t("notifications.mark_all_read") as string;
    markAllBtn.addEventListener("click", () => cbs.onMarkAllRead());
    headerActions.append(markAllBtn);
  }

  if (cbs.onOpenSettings) {
    const settingsBtn = document.createElement("button");
    settingsBtn.className = "notification-settings-btn";
    settingsBtn.type = "button";
    settingsBtn.textContent = "\u2699\uFE0F";
    settingsBtn.title = t("notifications.prefs_title") as string;
    settingsBtn.setAttribute("aria-label", t("notifications.prefs_title") as string);
    settingsBtn.addEventListener("click", () => cbs.onOpenSettings!());
    headerActions.append(settingsBtn);
  }

  if (cbs.onClose) {
    const closeBtn = document.createElement("button");
    closeBtn.className = "notification-panel-close";
    closeBtn.type = "button";
    closeBtn.textContent = "\u00D7";
    closeBtn.title = t("common.close") as string;
    closeBtn.setAttribute("aria-label", t("common.close") as string);
    closeBtn.addEventListener("click", () => cbs.onClose!());
    headerActions.append(closeBtn);
  }

  header.append(title, headerActions);
  container.append(header);

  const content = document.createElement("div");
  content.id = "notification-panel-content";
  content.setAttribute("role", "tabpanel");
  content.setAttribute("aria-labelledby", `notification-tab-${activeView}`);

  if (cbs.onViewChange) {
    const tabs = document.createElement("div");
    tabs.className = "notification-panel-tabs";
    tabs.setAttribute("role", "tablist");
    tabs.setAttribute("aria-label", t("notifications.views") as string);
    const views = ["inbox", "log"] as const;
    const tabButtons: HTMLButtonElement[] = [];
    const requestView = (view: "inbox" | "log"): void => {
      if (view !== activeView) cbs.onViewChange?.(view);
    };
    for (const view of views) {
      const tab = document.createElement("button");
      tab.type = "button";
      const selected = activeView === view;
      tab.id = `notification-tab-${view}`;
      tab.className = `notification-panel-tab${selected ? " active" : ""}`;
      tab.textContent = t(`notifications.tab_${view}`) as string;
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-selected", String(selected));
      tab.setAttribute("aria-controls", content.id);
      tab.tabIndex = selected ? 0 : -1;
      tab.addEventListener("click", () => requestView(view));
      tabButtons.push(tab);
      tabs.append(tab);
    }
    tabs.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      const currentIndex = tabButtons.findIndex((tab) => tab === document.activeElement);
      if (currentIndex < 0) return;
      let nextIndex = currentIndex;
      if (event.key === "ArrowLeft") {
        nextIndex = (currentIndex - 1 + tabButtons.length) % tabButtons.length;
      } else if (event.key === "ArrowRight") {
        nextIndex = (currentIndex + 1) % tabButtons.length;
      } else if (event.key === "Home") {
        nextIndex = 0;
      } else if (event.key === "End") {
        nextIndex = tabButtons.length - 1;
      }
      const next = tabButtons[nextIndex];
      if (!next) return;
      event.preventDefault();
      next.focus();
      const view = next.id === "notification-tab-log" ? "log" : "inbox";
      requestView(view);
    });
    container.append(tabs);
  }

  if (notifications.length === 0) {
    const empty = document.createElement("div");
    empty.className = "notification-empty";
    const emptyText = document.createElement("div");
    emptyText.className = "notification-empty-text";
    emptyText.textContent = t("notifications.empty") as string;
    const emptyHint = document.createElement("div");
    emptyHint.className = "notification-empty-hint";
    emptyHint.textContent = t(
      cbs.view === "log" ? "notifications.log_empty_hint" : "notifications.empty_hint",
    ) as string;
    empty.append(emptyText, emptyHint);
    content.append(empty);
  } else {
    for (const n of notifications) {
      content.append(createNotificationItem(n, cbs));
    }
  }
  container.append(content);
}

export function renderNotificationPreferencesForm(
  container: HTMLElement,
  prefs: NotificationPreferences,
  onSave: (updated: Partial<NotificationPreferences>) => void,
): void {
  container.replaceChildren();
  container.setAttribute("role", "dialog");
  container.setAttribute("aria-modal", "true");
  container.removeAttribute("aria-labelledby");
  container.setAttribute("aria-label", t("notifications.prefs_title") as string);

  const form = document.createElement("div");
  form.className = "notification-prefs-form";

  const titleEl = document.createElement("h3");
  titleEl.style.marginBottom = "var(--sp-3)";
  titleEl.textContent = t("notifications.prefs_title") as string;
  form.append(titleEl);

  const state: Partial<NotificationPreferences> = {
    in_app_enabled: prefs.in_app_enabled,
    email_enabled: prefs.email_enabled,
    email_address: prefs.email_address,
    digest_frequency: prefs.digest_frequency,
    quiet_hours_json: prefs.quiet_hours_json,
    task_due_enabled: prefs.task_due_enabled,
    task_overdue_enabled: prefs.task_overdue_enabled,
  };

  function addToggleRow(label: string, key: keyof NotificationPreferences, initialValue: boolean): void {
    const row = document.createElement("div");
    row.className = "notification-prefs-row";
    const lbl = document.createElement("span");
    lbl.className = "notification-prefs-label";
    lbl.textContent = label;
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = `notification-prefs-toggle${initialValue ? " active" : ""}`;
    toggle.setAttribute("aria-label", label);
    toggle.setAttribute("aria-pressed", String(initialValue));
    toggle.addEventListener("click", () => {
      const current = toggle.classList.contains("active");
      toggle.classList.toggle("active", !current);
      toggle.setAttribute("aria-pressed", String(!current));
      (state as Record<string, unknown>)[key] = !current;
    });
    row.append(lbl, toggle);
    form.append(row);
  }

  addToggleRow(t("notifications.prefs_in_app") as string, "in_app_enabled", prefs.in_app_enabled);
  addToggleRow(t("notifications.prefs_email") as string, "email_enabled", prefs.email_enabled);

  // Email address
  const emailRow = document.createElement("div");
  emailRow.className = "notification-prefs-row";
  emailRow.style.flexDirection = "column";
  emailRow.style.alignItems = "stretch";
  const emailLabel = document.createElement("label");
  emailLabel.className = "notification-prefs-label";
  emailLabel.textContent = t("notifications.prefs_email_address") as string;
  const emailInput = document.createElement("input");
  emailInput.id = "notification-prefs-email-address";
  emailLabel.htmlFor = emailInput.id;
  emailInput.type = "email";
  emailInput.value = prefs.email_address;
  emailInput.style.marginTop = "var(--sp-1)";
  emailInput.style.padding = "var(--sp-2)";
  emailInput.style.fontSize = "0.85rem";
  emailInput.addEventListener("input", () => {
    state.email_address = emailInput.value;
  });
  emailRow.append(emailLabel, emailInput);
  form.append(emailRow);

  // Digest frequency
  const digestRow = document.createElement("div");
  digestRow.className = "notification-prefs-row";
  const digestLabel = document.createElement("label");
  digestLabel.className = "notification-prefs-label";
  digestLabel.textContent = t("notifications.prefs_digest") as string;
  const digestSelect = document.createElement("select");
  digestSelect.id = "notification-prefs-digest-frequency";
  digestLabel.htmlFor = digestSelect.id;
  digestSelect.style.padding = "var(--sp-1) var(--sp-2)";
  digestSelect.style.fontSize = "0.85rem";
  for (const opt of ["none", "daily", "weekly"] as const) {
    const option = document.createElement("option");
    option.value = opt;
    option.textContent = t(`notifications.prefs_digest_${opt}`) as string;
    if (prefs.digest_frequency === opt) option.selected = true;
    digestSelect.append(option);
  }
  digestSelect.addEventListener("change", () => {
    state.digest_frequency = digestSelect.value as "none" | "daily" | "weekly";
  });
  digestRow.append(digestLabel, digestSelect);
  form.append(digestRow);

  // Quiet hours (shown when email enabled)
  const quietRow = document.createElement("div");
  quietRow.className = "notification-prefs-row";
  quietRow.style.flexDirection = "column";
  quietRow.style.alignItems = "stretch";
  const quietLabel = document.createElement("span");
  quietLabel.className = "notification-prefs-label";
  quietLabel.textContent = t("notifications.prefs_quiet_hours") as string;
  const quietInputs = document.createElement("div");
  quietInputs.style.display = "flex";
  quietInputs.style.gap = "var(--sp-2)";
  quietInputs.style.alignItems = "center";
  quietInputs.style.marginTop = "var(--sp-1)";
  const qh = (prefs.quiet_hours_json ?? {}) as Record<string, string>;
  const quietStart = document.createElement("input");
  quietStart.id = "notification-prefs-quiet-start";
  quietStart.type = "time";
  quietStart.value = qh["start"] ?? "";
  quietStart.style.padding = "var(--sp-1)";
  quietStart.style.fontSize = "0.85rem";
  quietStart.setAttribute(
    "aria-label",
    `${quietLabel.textContent} ${t("attention.preferences.start")}`,
  );
  const quietStartLabel = document.createElement("label");
  quietStartLabel.htmlFor = quietStart.id;
  quietStartLabel.textContent = t("attention.preferences.start") as string;
  const quietSep = document.createElement("span");
  quietSep.textContent = "\u2013";
  const quietEnd = document.createElement("input");
  quietEnd.id = "notification-prefs-quiet-end";
  quietEnd.type = "time";
  quietEnd.value = qh["end"] ?? "";
  quietEnd.style.padding = "var(--sp-1)";
  quietEnd.style.fontSize = "0.85rem";
  quietEnd.setAttribute(
    "aria-label",
    `${quietLabel.textContent} ${t("attention.preferences.end")}`,
  );
  const quietEndLabel = document.createElement("label");
  quietEndLabel.htmlFor = quietEnd.id;
  quietEndLabel.textContent = t("attention.preferences.end") as string;
  function updateQuietHours(): void {
    if (quietStart.value && quietEnd.value) {
      state.quiet_hours_json = { start: quietStart.value, end: quietEnd.value };
    } else {
      state.quiet_hours_json = {};
    }
  }
  quietStart.addEventListener("change", updateQuietHours);
  quietEnd.addEventListener("change", updateQuietHours);
  quietInputs.append(
    quietStartLabel,
    quietStart,
    quietSep,
    quietEndLabel,
    quietEnd,
  );
  quietRow.append(quietLabel, quietInputs);
  form.append(quietRow);

  const ruleState: Record<string, NotificationRulePreference> = {};
  for (const policy of prefs.policy) {
    const current = prefs.notification_rules[policy.key];
    ruleState[policy.key] = {
      in_app_enabled: current?.in_app_enabled ?? policy.default_in_app_enabled,
      email_enabled: current?.email_enabled ?? policy.default_email_enabled,
      min_severity: current?.min_severity ?? policy.default_min_severity,
    };
  }
  state.notification_rules = ruleState;

  const rulesTitle = document.createElement("h4");
  rulesTitle.className = "notification-prefs-section-title";
  rulesTitle.textContent = t("notifications.prefs_categories") as string;
  form.append(rulesTitle);

  const grouped = new Map<string, typeof prefs.policy>();
  for (const policy of prefs.policy) {
    if (!policy.user_configurable) continue;
    const rows = grouped.get(policy.group) ?? [];
    rows.push(policy);
    grouped.set(policy.group, rows);
  }

  for (const [group, policies] of grouped) {
    const groupEl = document.createElement("div");
    groupEl.className = "notification-prefs-group";
    const groupTitle = document.createElement("div");
    groupTitle.className = "notification-prefs-group-title";
    groupTitle.textContent = t(POLICY_GROUP_KEYS[group] ?? group) as string;
    groupEl.append(groupTitle);

    for (const policy of policies) {
      const rule = ruleState[policy.key] ?? {
        in_app_enabled: policy.default_in_app_enabled,
        email_enabled: policy.default_email_enabled,
        min_severity: policy.default_min_severity,
      };
      ruleState[policy.key] = rule;
      const row = document.createElement("div");
      row.className = "notification-prefs-rule-row";

      const label = document.createElement("div");
      label.className = "notification-prefs-rule-label";
      label.textContent = policyLabel(policy.key);

      const controls = document.createElement("div");
      controls.className = "notification-prefs-rule-controls";

      function makeRuleToggle(
        field: "in_app_enabled" | "email_enabled",
        labelKey: string,
      ): HTMLButtonElement {
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = `notification-prefs-chip${rule[field] ? " active" : ""}`;
        const channelLabel = t(labelKey) as string;
        toggle.textContent = channelLabel;
        toggle.setAttribute(
          "aria-label",
          `${policyLabel(policy.key)}: ${channelLabel}`,
        );
        toggle.setAttribute("aria-pressed", String(rule[field]));
        toggle.addEventListener("click", () => {
          rule[field] = !rule[field];
          toggle.classList.toggle("active", rule[field]);
          toggle.setAttribute("aria-pressed", String(rule[field]));
          state.notification_rules = ruleState;
        });
        return toggle;
      }

      controls.append(
        makeRuleToggle("in_app_enabled", "notifications.channel_in_app"),
        makeRuleToggle("email_enabled", "notifications.channel_email"),
      );

      if (policy.supports_severity) {
        const select = document.createElement("select");
        select.className = "notification-prefs-severity";
        select.id = `notification-prefs-severity-${policy.key}`;
        select.setAttribute(
          "aria-label",
          `${policyLabel(policy.key)}: ${t("notifications.prefs_min_severity")}`,
        );
        for (const severity of ["low", "normal", "high", "critical"] as const) {
          const option = document.createElement("option");
          option.value = severity;
          option.textContent = t(`notifications.severity_${severity}`) as string;
          if (rule.min_severity === severity) option.selected = true;
          select.append(option);
        }
        select.addEventListener("change", () => {
          rule.min_severity = select.value as NotificationRulePreference["min_severity"];
          state.notification_rules = ruleState;
        });
        controls.append(select);
      }

      row.append(label, controls);
      groupEl.append(row);
    }
    form.append(groupEl);
  }

  // Save button
  const saveRow = document.createElement("div");
  saveRow.style.marginTop = "var(--sp-3)";
  saveRow.style.textAlign = "right";
  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "btn-primary";
  saveBtn.textContent = t("common.save") as string;
  saveBtn.addEventListener("click", () => onSave(state));
  saveRow.append(saveBtn);
  form.append(saveRow);

  container.append(form);
}
