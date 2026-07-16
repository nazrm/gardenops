import { t } from "../core/i18n";
import type { OfflineDraft } from "../core/models";
import { canRetryFailedDraft } from "../services/offlineQueue";

export interface OfflineIndicatorCallbacks {
  onDiscard: (draft: OfflineDraft) => void;
  onRetry: (draft: OfflineDraft) => void;
  onSyncNow: () => void;
}

export interface OfflineIndicatorState {
  canDiscardDrafts?: boolean;
  canRetryDrafts?: boolean;
  failedDrafts: OfflineDraft[];
  online: boolean;
  pendingCount: number;
  syncingCount: number;
}

function firstText(
  payload: Record<string, unknown>,
  keys: readonly string[],
): string {
  for (const key of keys) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function serializedMediaName(payload: Record<string, unknown>): string {
  const media = payload["_serialized_media"];
  if (!Array.isArray(media)) return "";
  const first = media[0];
  if (!first || typeof first !== "object") return "";
  const name = (first as { name?: unknown }).name;
  return typeof name === "string" ? name.trim() : "";
}

function failedDraftLabel(draft: OfflineDraft): string {
  const taskId = String(draft.payload["task_id"] ?? "").trim();
  if (taskId) {
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

  const detailsByType: Record<string, { heading: string; keys: string[] }> = {
    journal: { heading: t("journal.title"), keys: ["title", "notes"] },
    issue_create: { heading: t("issues.title"), keys: ["title", "description"] },
    harvest_create: { heading: t("harvest.title"), keys: ["notes", "occurred_on"] },
  };
  const details = detailsByType[draft.type];
  if (details) {
    const label = firstText(draft.payload, details.keys);
    return label ? `${details.heading}: ${label}` : details.heading;
  }

  if (draft.type === "plant_media_upload" || draft.type === "plot_media_upload") {
    const target = firstText(draft.payload, ["target_label", "target_id"]);
    const file = serializedMediaName(draft.payload);
    const subject = [target, file].filter(Boolean).join(": ");
    return subject || t("media.untitled");
  }

  return t("offline.failed_draft", { type: draft.type });
}

function retryLabel(draft: OfflineDraft): string {
  return draft.last_status === 409 || draft.last_status === 410
    ? t("offline.retry_as_new")
    : t("offline.retry");
}

export function renderOfflineIndicator(
  container: HTMLElement,
  state: OfflineIndicatorState,
  callbacks?: OfflineIndicatorCallbacks,
): void {
  const failuresExpanded = container.querySelector(".offline-indicator-toggle")
    ?.getAttribute("aria-expanded") === "true";
  container.replaceChildren();
  const {
    canDiscardDrafts = true,
    canRetryDrafts = true,
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
  const badge = document.createElement(failedDrafts.length > 0 ? "button" : "span");
  badge.className = "offline-indicator";

  if (failedDrafts.length > 0) {
    badge.classList.add("offline-indicator--failed");
    badge.classList.add("offline-indicator-toggle");
    (badge as HTMLButtonElement).type = "button";
    badge.setAttribute("aria-controls", "offline-failures-panel");
    badge.setAttribute("aria-expanded", String(failuresExpanded));
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

  if (callbacks && canRetryDrafts && failedDrafts.length === 0
    && online && pendingCount > 0 && syncingCount === 0) {
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
  failures.id = "offline-failures-panel";
  failures.className = "offline-failures";
  failures.setAttribute("role", "alert");
  failures.setAttribute("aria-label", t("offline.failed_work"));
  failures.hidden = !failuresExpanded;
  badge.addEventListener("click", () => {
    const expanded = badge.getAttribute("aria-expanded") === "true";
    badge.setAttribute("aria-expanded", String(!expanded));
    failures.hidden = expanded;
  });
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
    if (callbacks && (canRetryDrafts || canDiscardDrafts)) {
      const actions = document.createElement("div");
      actions.className = "offline-failure-actions";
      if (canRetryDrafts && canRetryFailedDraft(draft)) {
        const retry = document.createElement("button");
        retry.type = "button";
        retry.className = "offline-retry-btn";
        retry.textContent = retryLabel(draft);
        retry.addEventListener("click", () => callbacks.onRetry(draft));
        actions.appendChild(retry);
      }
      if (canDiscardDrafts) {
        const discard = document.createElement("button");
        discard.type = "button";
        discard.className = "offline-discard-btn";
        discard.textContent = t("offline.discard");
        discard.addEventListener("click", () => callbacks.onDiscard(draft));
        actions.appendChild(discard);
      }
      row.appendChild(actions);
    }
    failures.appendChild(row);
  }
  container.appendChild(failures);
}
