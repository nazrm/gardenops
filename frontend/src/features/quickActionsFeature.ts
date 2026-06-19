import type { AppContext } from "../core/appContext";
import type { QuickActionCallbacks } from "../components/quickActions";
import { t } from "../core/i18n";
import {
  renderQuickActionSheet,
  renderTaskQuickComplete,
  renderTaskQuickSnooze,
} from "../components/quickActions";
import {
  fetchTasksApi,
  taskActionApi,
  getApiErrorMessage,
} from "../services/api";
import {
  isOnline,
  enqueueDraft,
} from "../services/offlineQueue";
import { showIdentifyPlantModal } from "../components/identifyPlant";

let ctx: AppContext;
let quickActionSheetOpen = false;
let escapeHandler: ((e: KeyboardEvent) => void) | null = null;

export function isQuickActionSheetOpen(): boolean {
  return quickActionSheetOpen;
}

export function initQuickActionsFeature(
  appCtx: AppContext,
): void {
  ctx = appCtx;

  document
    .getElementById("mobile-fab")
    ?.addEventListener("click", toggleQuickActionSheet);
  document
    .getElementById("mobile-fab-backdrop")
    ?.addEventListener("click", closeQuickActionSheet);
}

function getQuickActionCallbacks(): QuickActionCallbacks {
  return {
    onCompleteTask: () =>
      void showTaskQuickComplete(),
    onLogJournal: () => {
      closeQuickActionSheet();
      ctx.navigateToSubMode("journal");
      void ctx.openJournalComposer();
    },
    onReportIssue: () => {
      closeQuickActionSheet();
      ctx.navigateToSubMode("issues");
      void ctx.openIssueForm();
    },
    onLogHarvest: () => {
      closeQuickActionSheet();
      ctx.navigateToSubMode("harvest");
      void ctx.openHarvestForm();
    },
    onSnoozeTask: () =>
      void showTaskQuickSnooze(),
    onIdentifyPlant: () => {
      closeQuickActionSheet();
      showIdentifyPlantModal({
        onAddPlant: (prefill) => {
          ctx.navigateToSubMode("plants");
          ctx.openCreatePlantDialog(
            undefined,
            prefill,
          );
        },
        onClose: () => {},
      });
    },
  };
}

export function toggleQuickActionSheet(): void {
  if (!quickActionSheetOpen && !ctx.ensureWriteAccess()) {
    return;
  }
  quickActionSheetOpen = !quickActionSheetOpen;
  const sheet = document.getElementById(
    "mobile-quick-actions",
  );
  const backdrop = document.getElementById(
    "mobile-fab-backdrop",
  );
  const fab = document.getElementById("mobile-fab");
  if (sheet)
    sheet.setAttribute(
      "aria-hidden",
      String(!quickActionSheetOpen),
    );
  if (backdrop)
    backdrop.setAttribute(
      "aria-hidden",
      String(!quickActionSheetOpen),
    );
  if (fab)
    fab.classList.toggle(
      "open",
      quickActionSheetOpen,
    );

  if (quickActionSheetOpen) {
    const content = document.getElementById(
      "mobile-quick-actions-content",
    );
    if (content) {
      renderQuickActionSheet(
        content,
        getQuickActionCallbacks(),
      );
    }
    escapeHandler = (e: KeyboardEvent) => { if (e.key === "Escape") closeQuickActionSheet(); };
    window.addEventListener("keydown", escapeHandler);
  } else if (escapeHandler) {
    window.removeEventListener("keydown", escapeHandler);
    escapeHandler = null;
  }
}

export function closeQuickActionSheet(): void {
  quickActionSheetOpen = false;
  const sheet = document.getElementById(
    "mobile-quick-actions",
  );
  const backdrop = document.getElementById(
    "mobile-fab-backdrop",
  );
  const fab = document.getElementById("mobile-fab");
  if (sheet)
    sheet.setAttribute("aria-hidden", "true");
  if (backdrop)
    backdrop.setAttribute("aria-hidden", "true");
  if (fab) fab.classList.remove("open");
  if (escapeHandler) {
    window.removeEventListener("keydown", escapeHandler);
    escapeHandler = null;
  }
}

async function showTaskQuickComplete(): Promise<void> {
  const content = document.getElementById(
    "mobile-quick-actions-content",
  );
  if (!content) return;
  try {
    const result = await fetchTasksApi({
      view: "today",
      limit: 20,
      offset: 0,
    });
    const pending = result.tasks.filter(
      (tk) => tk.status === "pending",
    );
    renderTaskQuickComplete(
      content,
      pending.map((tk) => ({
        id: tk.id,
        title: tk.title,
        task_type: tk.task_type,
      })),
      async (taskId) => {
        if (!isOnline()) {
          await enqueueDraft("task_complete", {
            task_id: taskId,
          });
          ctx.showToast(
            t("offline.draft_saved"),
            "success",
          );
          void ctx.refreshOfflineIndicator();
          return;
        }
        try {
          await taskActionApi(taskId, {
            action: "complete",
          });
          ctx.showToast(
            t("tasks.action_success", {
              action: "complete",
            }),
            "success",
          );
          void ctx.refreshBadgeCounts();
          await showTaskQuickComplete();
        } catch (err) {
          ctx.showToast(
            getApiErrorMessage(err),
            "error",
          );
        }
      },
      () => {
        const c = document.getElementById(
          "mobile-quick-actions-content",
        );
        if (c)
          renderQuickActionSheet(
            c,
            getQuickActionCallbacks(),
          );
      },
    );
  } catch (err) {
    ctx.showToast(
      getApiErrorMessage(err),
      "error",
    );
  }
}

async function showTaskQuickSnooze(): Promise<void> {
  const content = document.getElementById(
    "mobile-quick-actions-content",
  );
  if (!content) return;
  try {
    const result = await fetchTasksApi({
      view: "today",
      limit: 20,
      offset: 0,
    });
    const pending = result.tasks.filter(
      (tk) => tk.status === "pending",
    );
    renderTaskQuickSnooze(
      content,
      pending.map((tk) => ({
        id: tk.id,
        title: tk.title,
        task_type: tk.task_type,
      })),
      async (taskId) => {
        const tomorrow = new Date();
        tomorrow.setDate(tomorrow.getDate() + 1);
        const snoozeDate = tomorrow
          .toISOString()
          .slice(0, 10);
        if (!isOnline()) {
          await enqueueDraft("task_snooze", {
            task_id: taskId,
            snooze_until: snoozeDate,
          });
          ctx.showToast(
            t("offline.draft_saved"),
            "success",
          );
          void ctx.refreshOfflineIndicator();
          return;
        }
        try {
          await taskActionApi(taskId, {
            action: "snooze",
            snooze_until: snoozeDate,
          });
          ctx.showToast(
            t("tasks.action_success", {
              action: "snooze",
            }),
            "success",
          );
          void ctx.refreshBadgeCounts();
          await showTaskQuickSnooze();
        } catch (err) {
          ctx.showToast(
            getApiErrorMessage(err),
            "error",
          );
        }
      },
      () => {
        const c = document.getElementById(
          "mobile-quick-actions-content",
        );
        if (c)
          renderQuickActionSheet(
            c,
            getQuickActionCallbacks(),
          );
      },
    );
  } catch (err) {
    ctx.showToast(
      getApiErrorMessage(err),
      "error",
    );
  }
}
