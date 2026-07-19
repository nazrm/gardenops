import type { GardenTask, TaskType } from "../core/models";
import { t } from "../core/i18n";
import { createModal } from "../components/dialogCore";
import type { TaskActionRequest } from "../services/api";

type CompletionTask = Pick<GardenTask, "task_type" | "plant_ids">;
type TaskActionLabelTask = Pick<GardenTask, "task_type" | "title">;

interface TaskCompletionDialogOptions {
  modalParent?: HTMLElement | null | undefined;
}

const CAPTURE_TASK_TYPES = new Set<TaskType>([
  "observe_bloom",
  "prune",
  "fertilize",
]);

export function needsCompletionSelection(task: CompletionTask): boolean {
  return CAPTURE_TASK_TYPES.has(task.task_type) && (task.plant_ids?.length ?? 0) > 1;
}

export function needsCompletionDialog(task: CompletionTask): boolean {
  return task.task_type === "observe_bloom" || needsCompletionSelection(task);
}

export function canQueueDefaultCompletionOffline(task: CompletionTask): boolean {
  return !needsCompletionDialog(task);
}

export function canQueueCompletionOffline(task: CompletionTask): boolean {
  return canQueueDefaultCompletionOffline(task) || task.task_type === "observe_bloom";
}

export function taskCompletionActionLabel(task: CompletionTask): string {
  return String(t(
    task.task_type === "observe_bloom"
      ? "tasks.action_record_outcome"
      : "tasks.action_complete",
  ));
}

export function offlineTaskActionLabels(
  task: TaskActionLabelTask,
  action: TaskActionRequest["action"],
): { action_label: string; task_label: string } {
  return {
    action_label: String(t(`tasks.action_${action}`)),
    task_label: task.title.trim() || String(t(`tasks.type_${task.task_type}`)),
  };
}

export function defaultSelectedPlantIds(task: CompletionTask): Set<string> {
  const ids = task.plant_ids ?? [];
  return new Set(ids.length <= 5 ? ids : []);
}

export function openTaskCompletionDialog(
  task: CompletionTask,
  plantNames: Map<string, string>,
  onConfirm: (
    body: TaskActionRequest,
  ) => boolean | void | Promise<boolean | void>,
  options: TaskCompletionDialogOptions = {},
): void {
  const selected = defaultSelectedPlantIds(task);
  const { dialog, close } = createModal(String(t("tasks.complete_select_plants_title")), `
    <div class="modal-content task-completion-dialog">
      <h3></h3>
      <div class="task-completion-list"></div>
      <div class="task-completion-feedback" role="status" aria-live="polite"></div>
      <div class="button-row">
        <button type="button" class="task-completion-select-all"></button>
        <button type="button" class="task-completion-clear"></button>
        <button type="button" class="task-completion-not-seen"></button>
        <button type="button" class="confirm-yes"></button>
        <button type="button" class="confirm-no"></button>
      </div>
    </div>
  `, { modalParent: options.modalParent });
  dialog.querySelector("h3")!.textContent = String(t("tasks.complete_select_plants_title"));
  const list = dialog.querySelector<HTMLElement>(".task-completion-list")!;
  const feedback = dialog.querySelector<HTMLElement>(".task-completion-feedback")!;
  const confirm = dialog.querySelector<HTMLButtonElement>(".confirm-yes")!;
  const selectAll = dialog.querySelector<HTMLButtonElement>(".task-completion-select-all")!;
  const clear = dialog.querySelector<HTMLButtonElement>(".task-completion-clear")!;
  const cancel = dialog.querySelector<HTMLButtonElement>(".confirm-no")!;
  const notSeen = dialog.querySelector<HTMLButtonElement>(".task-completion-not-seen")!;
  const checkboxes: HTMLInputElement[] = [];
  let submitting = false;
  let submitError = "";

  const syncState = (): void => {
    for (const checkbox of checkboxes) {
      checkbox.checked = selected.has(checkbox.value);
      checkbox.disabled = submitting;
    }
    const selectionRequired = needsCompletionSelection(task);
    const selectionMissing = selectionRequired && selected.size === 0;
    confirm.disabled = submitting || selectionMissing;
    notSeen.disabled = submitting || selectionMissing;
    selectAll.disabled = submitting;
    clear.disabled = submitting;
    cancel.disabled = submitting;
    dialog.toggleAttribute("aria-busy", submitting);
    feedback.textContent = selectionMissing
      ? String(t("tasks.complete_select_one"))
      : submitError;
  };

  const submit = async (
    body: TaskActionRequest,
    submitButton: HTMLButtonElement,
  ): Promise<void> => {
    if (submitting) return;
    if (needsCompletionSelection(task) && selected.size === 0) {
      syncState();
      (checkboxes[0] ?? confirm).focus();
      return;
    }
    submitting = true;
    submitError = "";
    syncState();
    try {
      const result = await onConfirm(body);
      if (result === false) {
        submitError = String(t("tasks.dialog_submit_failed"));
        return;
      }
      close();
    } catch {
      submitError = String(t("tasks.dialog_submit_failed"));
    } finally {
      if (dialog.isConnected) {
        submitting = false;
        syncState();
        if (submitError) submitButton.focus();
      }
    }
  };

  for (const plantId of task.plant_ids ?? []) {
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = plantId;
    checkbox.checked = selected.has(plantId);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) selected.add(plantId);
      else selected.delete(plantId);
      submitError = "";
      syncState();
    });
    checkboxes.push(checkbox);
    label.append(checkbox, document.createTextNode(plantNames.get(plantId) ?? plantId));
    list.appendChild(label);
  }

  selectAll.textContent = String(t("common.select_all"));
  selectAll.addEventListener("click", () => {
    for (const checkbox of checkboxes) selected.add(checkbox.value);
    submitError = "";
    syncState();
    confirm.focus();
  });

  clear.textContent = String(t("common.clear"));
  clear.addEventListener("click", () => {
    selected.clear();
    submitError = "";
    syncState();
    checkboxes[0]?.focus();
  });

  cancel.textContent = String(t("common.cancel"));
  cancel.addEventListener("click", close);
  if (task.task_type === "observe_bloom") {
    notSeen.textContent = String(t("tasks.action_not_seen_blooming"));
    notSeen.addEventListener("click", () => {
      const completed_plant_ids = [...selected];
      void submit({
        action: "complete",
        completed_plant_ids,
        completion_outcome: "not_seen_blooming_this_season",
      }, notSeen);
    });
  } else {
    notSeen.remove();
  }
  confirm.textContent = String(
    task.task_type === "observe_bloom"
      ? t("tasks.action_seen_blooming")
      : t("tasks.action_complete"),
  );
  confirm.addEventListener("click", () => {
    const completed_plant_ids = [...selected];
    void submit({
      action: "complete",
      completed_plant_ids,
      completion_outcome: "done",
    }, confirm);
  });
  syncState();
  (checkboxes[0] ?? confirm).focus();
}
