import { t } from "../core/i18n";
import type { OfflineDraft } from "../core/models";

export interface OfflineIndicatorCallbacks {
  onDiscard: (draft: OfflineDraft) => void;
  onRetry: (draft: OfflineDraft) => void;
  onSyncNow: () => void;
}

export interface OfflineIndicatorState {
  canManageDrafts?: boolean;
  failedDrafts: OfflineDraft[];
  online: boolean;
  pendingCount: number;
  syncingCount: number;
}

function failedDraftLabel(draft: OfflineDraft): string {
  const taskId = String(draft.payload["task_id"] ?? "").trim();
  if (!taskId) return t("offline.failed_draft", { type: draft.type });
  const taskLabel = typeof draft.payload["task_label"] === "string"
    && draft.payload["task_label"].trim()
    ? draft.payload["task_label"].trim()
    : t("offline.failed_task", { task: taskId });
  const actionLabel = typeof draft.payload["action_label"] === "string"
    && draft.payload["action_label"].trim()
    ? draft.payload["action_label"].trim()
    : t(`tasks.action_${draft.type.replace("task_", "")}`);
  return t("offline.failed_task_action", {
    action: actionLabel,
    task: taskLabel,
  });
}

export function renderOfflineIndicator(
  container: HTMLElement,
  state: OfflineIndicatorState,
  callbacks?: OfflineIndicatorCallbacks,
): void {
  container.replaceChildren();
  const {
    canManageDrafts = true,
    failedDrafts,
    online,
    pendingCount,
    syncingCount,
  } = state;

  if (online && pendingCount === 0 && syncingCount === 0 && failedDrafts.length === 0) {
    container.hidden = true;
    return;
  }

  container.hidden = false;
  const badge = document.createElement("span");
  badge.className = "offline-indicator";

  if (failedDrafts.length > 0) {
    badge.classList.add("offline-indicator--failed");
    const label = document.createElement("span");
    label.textContent = t("offline.indicator_failed");
    badge.appendChild(label);
  } else if (!online) {
    badge.classList.add("offline-indicator--offline");
    const label = document.createElement("span");
    label.textContent = t("offline.indicator_offline");
    badge.appendChild(label);
  } else if (syncingCount > 0) {
    badge.classList.add("offline-indicator--syncing");
    const label = document.createElement("span");
    label.textContent = t("offline.indicator_syncing");
    badge.appendChild(label);
  } else {
    badge.classList.add("offline-indicator--pending");
    const label = document.createElement("span");
    label.textContent = t("offline.indicator_pending_ready");
    badge.appendChild(label);
  }

  if (pendingCount > 0) {
    const count = document.createElement("span");
    count.className = "offline-indicator-count";
    count.textContent = ` ${t("offline.indicator_pending", { count: pendingCount })}`;
    badge.appendChild(count);
  }
  if (failedDrafts.length > 0) {
    const count = document.createElement("span");
    count.className = "offline-indicator-count offline-indicator-failed-count";
    count.textContent = ` ${t("offline.indicator_failed_count", { count: failedDrafts.length })}`;
    badge.appendChild(count);
  }

  if (callbacks && canManageDrafts && online && pendingCount > 0 && syncingCount === 0) {
    const btn = document.createElement("button");
    btn.className = "offline-sync-btn";
    btn.type = "button";
    btn.textContent = t("offline.sync_now");
    btn.addEventListener("click", callbacks.onSyncNow);
    badge.appendChild(btn);
  }

  container.appendChild(badge);

  if (failedDrafts.length === 0) return;
  const failures = document.createElement("section");
  failures.className = "offline-failures";
  failures.setAttribute("role", "alert");
  failures.setAttribute("aria-label", t("offline.failed_work"));
  const title = document.createElement("strong");
  title.textContent = t("offline.failed_work");
  failures.appendChild(title);
  for (const draft of failedDrafts) {
    const row = document.createElement("div");
    row.className = "offline-failure-row";
    const copy = document.createElement("div");
    const label = document.createElement("span");
    label.className = "offline-failure-label";
    label.textContent = failedDraftLabel(draft);
    const error = document.createElement("span");
    error.className = "offline-failure-error";
    error.textContent = draft.last_error || t("offline.failed_unknown");
    copy.append(label, error);
    row.appendChild(copy);
    if (callbacks && canManageDrafts) {
      const actions = document.createElement("div");
      actions.className = "offline-failure-actions";
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "offline-retry-btn";
      retry.textContent = t("offline.retry");
      retry.addEventListener("click", () => callbacks.onRetry(draft));
      const discard = document.createElement("button");
      discard.type = "button";
      discard.className = "offline-discard-btn";
      discard.textContent = t("offline.discard");
      discard.addEventListener("click", () => callbacks.onDiscard(draft));
      actions.append(retry, discard);
      row.appendChild(actions);
    }
    failures.appendChild(row);
  }
  container.appendChild(failures);
}
