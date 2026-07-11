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
import { taskSnoozePolicy } from "./taskSnoozePolicy";
import {
  canQueueDefaultCompletionOffline,
  needsCompletionDialog,
  openTaskCompletionDialog,
} from "./taskCompletionFlow";

let ctx: AppContext;
let quickActionSheetOpen = false;
let escapeHandler: ((e: KeyboardEvent) => void) | null = null;
type IdentifyPlantModule = typeof import("../components/identifyPlant");
let identifyPlantModulePromise: Promise<IdentifyPlantModule> | null = null;

function showIdentifyPlantModalLazy(
  ...params: Parameters<IdentifyPlantModule["showIdentifyPlantModal"]>
): void {
  identifyPlantModulePromise ??= import("../components/identifyPlant")
    .catch((err) => {
      identifyPlantModulePromise = null;
      throw err;
    });
  void identifyPlantModulePromise
    .then((mod) => mod.showIdentifyPlantModal(...params))
    .catch((err) => {
      console.error("Failed to load identify plant modal", err);
    });
}

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
      showIdentifyPlantModalLazy({
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
    const pendingById = new Map(pending.map((task) => [task.id, task]));
    renderTaskQuickComplete(
      content,
      pending.map((tk) => ({
        id: tk.id,
        title: tk.title,
        task_type: tk.task_type,
      })),
      async (taskId) => {
        const task = pendingById.get(taskId);
        if (task && needsCompletionDialog(task)) {
          if (!isOnline()) {
            if (!canQueueDefaultCompletionOffline(task)) {
              ctx.showToast(t("tasks.complete_grouped_one_by_one"), "error");
              return;
            }
          } else {
            await ctx.ensurePlantsCacheLoaded();
            const plantNames = new Map(ctx.getPlants().map((plant) => [plant.plt_id, plant.name]));
            openTaskCompletionDialog(task, plantNames, (body) => {
              void (async () => {
                try {
                  await taskActionApi(taskId, body);
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
              })();
            });
            return;
          }
        }
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
    const pendingById = new Map(pending.map((task) => [task.id, task]));
    renderTaskQuickSnooze(
      content,
      pending.map((tk) => ({
        id: tk.id,
        title: tk.title,
        task_type: tk.task_type,
      })),
      async (taskId) => {
        const task = pendingById.get(taskId);
        if (!task) return;
        const policy = taskSnoozePolicy(task);
        const snoozeDate = policy.immediate
          ? policy.defaultDate
          : window.prompt(
            policy.warning
              ? `${policy.warning}\n\n${t("tasks.snooze_prompt")}`
              : t("tasks.snooze_prompt"),
            policy.defaultDate,
          );
        if (!snoozeDate) return;
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
