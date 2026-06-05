import { t } from "../core/i18n";

export interface OfflineIndicatorCallbacks {
  onSyncNow: () => void;
}

export function renderOfflineIndicator(
  container: HTMLElement,
  pendingCount: number,
  online: boolean,
  callbacks?: OfflineIndicatorCallbacks,
): void {
  container.replaceChildren();

  if (online && pendingCount === 0) {
    container.hidden = true;
    return;
  }

  container.hidden = false;
  const badge = document.createElement("span");
  badge.className = "offline-indicator";

  if (!online) {
    badge.classList.add("offline-indicator--offline");
    const label = document.createElement("span");
    label.textContent = t("offline.indicator_offline");
    badge.appendChild(label);

    if (pendingCount > 0) {
      const count = document.createElement("span");
      count.className = "offline-indicator-count";
      count.textContent = ` ${t("offline.indicator_pending", { count: pendingCount })}`;
      badge.appendChild(count);
    }
  } else {
    badge.classList.add("offline-indicator--syncing");
    const label = document.createElement("span");
    label.textContent = t("offline.indicator_syncing");
    badge.appendChild(label);

    const count = document.createElement("span");
    count.className = "offline-indicator-count";
    count.textContent = ` ${t("offline.indicator_pending", { count: pendingCount })}`;
    badge.appendChild(count);

    if (callbacks) {
      const btn = document.createElement("button");
      btn.className = "offline-sync-btn";
      btn.type = "button";
      btn.textContent = t("offline.sync_now");
      btn.addEventListener("click", callbacks.onSyncNow);
      badge.appendChild(btn);
    }
  }

  container.appendChild(badge);
}
