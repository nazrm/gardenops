import type { AppContext } from "../core/appContext";
import type { GardenTask } from "../core/models";
import type { QuickActionCallbacks } from "../components/quickActions";
import { t } from "../core/i18n";
import {
  appendQuickActionSnoozeNotice,
  renderQuickActionSheet,
  renderTaskQuickComplete,
  renderTaskQuickSnooze,
} from "../components/quickActions";
import { trapFocus } from "../components/dialogCore";
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
  getTaskSnoozeCorrectionNotice,
  openTaskDateDialog,
} from "./taskSnoozeFlow";
import {
  canQueueDefaultCompletionOffline,
  needsCompletionDialog,
  openTaskCompletionDialog,
} from "./taskCompletionFlow";

let ctx: AppContext;
let quickActionSheetOpen = false;
let escapeHandler: ((e: KeyboardEvent) => void) | null = null;
let releaseFocusTrap: (() => void) | null = null;
let inertBackgroundElements: Array<{
  element: HTMLElement;
  wasInert: boolean;
}> = [];
const QUICK_ACTION_TASK_LIMIT = 200;
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

  const fab = document.getElementById("mobile-fab");
  fab?.setAttribute("aria-controls", "mobile-quick-actions");
  fab?.setAttribute("aria-haspopup", "dialog");
  fab?.setAttribute("aria-expanded", "false");
  fab?.addEventListener("click", toggleQuickActionSheet);
  document
    .getElementById("mobile-fab-backdrop")
    ?.addEventListener("click", () => closeQuickActionSheet());
  document
    .getElementById("mobile-quick-actions-close-btn")
    ?.addEventListener("click", () => closeQuickActionSheet());
}

function getQuickActionCallbacks(): QuickActionCallbacks {
  return {
    onCompleteTask: () =>
      void showTaskQuickComplete(),
    onLogJournal: () => {
      closeQuickActionSheet(false);
      ctx.navigateToSubMode("journal");
      void ctx.openJournalComposer();
    },
    onReportIssue: () => {
      closeQuickActionSheet(false);
      ctx.navigateToSubMode("issues");
      void ctx.openIssueForm();
    },
    onLogHarvest: () => {
      closeQuickActionSheet(false);
      ctx.navigateToSubMode("harvest");
      void ctx.openHarvestForm();
    },
    onSnoozeTask: () =>
      void showTaskQuickSnooze(),
    onIdentifyPlant: () => {
      closeQuickActionSheet(false);
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

function quickActionSheet(): HTMLElement | null {
  const sheet = document.getElementById("mobile-quick-actions");
  return sheet instanceof HTMLElement ? sheet : null;
}

function quickActionContent(): HTMLElement | null {
  const content = document.getElementById("mobile-quick-actions-content");
  return content instanceof HTMLElement ? content : null;
}

function setQuickActionBackgroundInert(
  sheet: HTMLElement,
  active: boolean,
): void {
  if (active) {
    if (inertBackgroundElements.length > 0) return;
    const backdrop = document.getElementById("mobile-fab-backdrop");
    const shell = sheet.parentElement;
    if (!(shell instanceof HTMLElement)) return;
    inertBackgroundElements = Array.from(shell.children)
      .filter(
        (child): child is HTMLElement =>
          child instanceof HTMLElement && child !== sheet && child !== backdrop,
      )
      .map((element) => ({
        element,
        wasInert: element.hasAttribute("inert"),
      }));
    for (const { element } of inertBackgroundElements) {
      element.setAttribute("inert", "");
    }
    return;
  }

  for (const { element, wasInert } of inertBackgroundElements) {
    if (wasInert) {
      element.setAttribute("inert", "");
    } else {
      element.removeAttribute("inert");
    }
  }
  inertBackgroundElements = [];
}

function focusQuickActionSheet(preferContent = false): void {
  window.requestAnimationFrame(() => {
    if (!quickActionSheetOpen) return;
    const sheet = quickActionSheet();
    if (!sheet) return;
    const content = quickActionContent();
    const contentTarget = preferContent
      ? content?.querySelector<HTMLElement>(
        "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
      )
      : null;
    const closeButton = document.getElementById("mobile-quick-actions-close-btn");
    const target = contentTarget ?? closeButton ?? sheet;
    if (target instanceof HTMLElement) target.focus();
  });
}

function renderQuickActionHome(restoreFocus = false): void {
  const content = quickActionContent();
  if (!content) return;
  renderQuickActionSheet(content, getQuickActionCallbacks());
  if (restoreFocus) focusQuickActionSheet();
}

function openQuickActionSheet(): void {
  const sheet = quickActionSheet();
  if (!sheet) return;
  quickActionSheetOpen = true;
  sheet.setAttribute("aria-hidden", "false");
  sheet.removeAttribute("inert");
  document.getElementById("mobile-fab-backdrop")?.setAttribute("aria-hidden", "false");
  const fab = document.getElementById("mobile-fab");
  if (fab instanceof HTMLElement) {
    fab.classList.add("open");
    fab.setAttribute("aria-expanded", "true");
    fab.setAttribute("aria-label", t("quick_actions.close") as string);
  }
  setQuickActionBackgroundInert(sheet, true);
  renderQuickActionHome();
  releaseFocusTrap?.();
  releaseFocusTrap = trapFocus(sheet);
  escapeHandler = (event: KeyboardEvent) => {
    if (
      event.key !== "Escape" ||
      !(event.target instanceof Node) ||
      !sheet.contains(event.target)
    ) {
      return;
    }
    event.preventDefault();
    closeQuickActionSheet();
  };
  window.addEventListener("keydown", escapeHandler);
  focusQuickActionSheet();
}

export function toggleQuickActionSheet(): void {
  if (quickActionSheetOpen) {
    closeQuickActionSheet();
    return;
  }
  if (!ctx.ensureWriteAccess()) return;
  openQuickActionSheet();
}

export function closeQuickActionSheet(restoreFocus = true): void {
  const sheet = quickActionSheet();
  quickActionSheetOpen = false;
  releaseFocusTrap?.();
  releaseFocusTrap = null;
  if (escapeHandler) {
    window.removeEventListener("keydown", escapeHandler);
    escapeHandler = null;
  }
  if (sheet) {
    sheet.setAttribute("aria-hidden", "true");
    sheet.setAttribute("inert", "");
    setQuickActionBackgroundInert(sheet, false);
  }
  document.getElementById("mobile-fab-backdrop")?.setAttribute("aria-hidden", "true");
  const fab = document.getElementById("mobile-fab");
  if (fab instanceof HTMLElement) {
    fab.classList.remove("open");
    fab.setAttribute("aria-expanded", "false");
    fab.setAttribute("aria-label", t("quick_actions.title") as string);
    if (restoreFocus) {
      window.requestAnimationFrame(() => fab.focus());
    }
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
      limit: QUICK_ACTION_TASK_LIMIT,
      offset: 0,
    });
    const actionable = result.tasks.filter(
      (tk) => tk.status === "pending" || tk.status === "snoozed",
    );
    const actionableById = new Map(actionable.map((task) => [task.id, task]));
    if (!quickActionSheetOpen) return;
    renderTaskQuickComplete(
      content,
      actionable.map((tk) => ({
        id: tk.id,
        title: tk.title,
        task_type: tk.task_type,
      })),
      async (taskId) => {
        const task = actionableById.get(taskId);
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
        renderQuickActionHome(true);
      },
    );
    focusQuickActionSheet(true);
  } catch (err) {
    ctx.showToast(
      getApiErrorMessage(err),
      "error",
    );
  }
}

function openQuickSnoozeDateDialog(
  task: GardenTask,
  defaultDate: string,
  warning?: string,
): void {
  openTaskDateDialog({
    title: t("tasks.snooze_prompt") as string,
    defaultDate,
    onConfirm: (date) => void snoozeQuickTask(task, date),
    warning,
    onClose: () => focusQuickActionSheet(true),
  });
}

async function snoozeQuickTask(
  task: GardenTask,
  snoozeUntil: string,
): Promise<void> {
  const online = isOnline();
  try {
    if (!online) {
      await enqueueDraft("task_snooze", {
        task_id: task.id,
        snooze_until: snoozeUntil,
      });
      ctx.showToast(t("offline.draft_saved"), "success");
      void ctx.refreshOfflineIndicator();
    } else {
      await taskActionApi(task.id, {
        action: "snooze",
        snooze_until: snoozeUntil,
      });
      void ctx.refreshBadgeCounts();
    }
    await showTaskQuickSnooze({ task, snoozeUntil });
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

async function showTaskQuickSnooze(
  correction?: { task: GardenTask; snoozeUntil: string },
): Promise<void> {
  const content = quickActionContent();
  if (!content) return;
  try {
    const result = await fetchTasksApi({
      view: "today",
      limit: QUICK_ACTION_TASK_LIMIT,
      offset: 0,
    });
    const actionable = result.tasks.filter(
      (tk) => tk.status === "pending" || tk.status === "snoozed",
    );
    const actionableById = new Map(actionable.map((task) => [task.id, task]));
    if (!quickActionSheetOpen) return;
    const onSnoozeDate = (taskId: string): void => {
      const task = actionableById.get(taskId);
      if (!task) return;
      const policy = taskSnoozePolicy(task);
      openQuickSnoozeDateDialog(task, policy.defaultDate, policy.warning);
    };
    renderTaskQuickSnooze(
      content,
      actionable.map((tk) => ({
        id: tk.id,
        title: tk.title,
        task_type: tk.task_type,
      })),
      async (taskId) => {
        const task = actionableById.get(taskId);
        if (!task) return;
        const policy = taskSnoozePolicy(task);
        if (!policy.immediate) {
          openQuickSnoozeDateDialog(
            task,
            policy.defaultDate,
            policy.warning,
          );
          return;
        }
        await snoozeQuickTask(task, policy.defaultDate);
      },
      onSnoozeDate,
      () => {
        renderQuickActionHome(true);
      },
    );
    if (correction) {
      appendQuickActionSnoozeNotice(
        content,
        getTaskSnoozeCorrectionNotice(
          correction.snoozeUntil,
          () => openQuickSnoozeDateDialog(correction.task, correction.snoozeUntil),
        ),
      );
      focusQuickActionSheet(true);
    } else {
      focusQuickActionSheet(true);
    }
  } catch (err) {
    ctx.showToast(
      getApiErrorMessage(err),
      "error",
    );
  }
}
