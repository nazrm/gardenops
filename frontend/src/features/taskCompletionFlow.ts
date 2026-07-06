import type { GardenTask, TaskType } from "../core/models";
import { t } from "../core/i18n";
import { createModal } from "../components/dialogCore";
import type { TaskActionRequest } from "../services/api";

type CompletionTask = Pick<GardenTask, "task_type" | "plant_ids">;

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
  return task.task_type === "observe_bloom" && (task.plant_ids?.length ?? 0) <= 1;
}

export function defaultSelectedPlantIds(task: CompletionTask): Set<string> {
  const ids = task.plant_ids ?? [];
  return new Set(ids.length <= 5 ? ids : []);
}

export function openTaskCompletionDialog(
  task: CompletionTask,
  plantNames: Map<string, string>,
  onConfirm: (body: TaskActionRequest) => void,
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
  `);
  dialog.querySelector("h3")!.textContent = String(t("tasks.complete_select_plants_title"));
  const list = dialog.querySelector<HTMLElement>(".task-completion-list")!;
  const feedback = dialog.querySelector<HTMLElement>(".task-completion-feedback")!;
  const confirm = dialog.querySelector<HTMLButtonElement>(".confirm-yes")!;
  const checkboxes: HTMLInputElement[] = [];

  const syncState = (): void => {
    for (const checkbox of checkboxes) {
      checkbox.checked = selected.has(checkbox.value);
    }
    confirm.disabled = selected.size === 0;
    feedback.textContent = selected.size === 0 ? String(t("tasks.complete_select_one")) : "";
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
      syncState();
    });
    checkboxes.push(checkbox);
    label.append(checkbox, document.createTextNode(plantNames.get(plantId) ?? plantId));
    list.appendChild(label);
  }

  const selectAll = dialog.querySelector<HTMLButtonElement>(".task-completion-select-all")!;
  selectAll.textContent = String(t("common.select_all"));
  selectAll.addEventListener("click", () => {
    for (const checkbox of checkboxes) selected.add(checkbox.value);
    syncState();
    confirm.focus();
  });

  const clear = dialog.querySelector<HTMLButtonElement>(".task-completion-clear")!;
  clear.textContent = String(t("common.clear"));
  clear.addEventListener("click", () => {
    selected.clear();
    syncState();
    checkboxes[0]?.focus();
  });

  dialog.querySelector<HTMLButtonElement>(".confirm-no")!.textContent = String(t("common.cancel"));
  dialog.querySelector<HTMLButtonElement>(".confirm-no")!.addEventListener("click", close);
  const notSeen = dialog.querySelector<HTMLButtonElement>(".task-completion-not-seen")!;
  if (task.task_type === "observe_bloom") {
    notSeen.textContent = String(t("tasks.action_not_seen_blooming"));
    notSeen.addEventListener("click", () => {
      const completed_plant_ids = [...selected];
      if (completed_plant_ids.length === 0) {
        syncState();
        checkboxes[0]?.focus();
        return;
      }
      onConfirm({
        action: "complete",
        completed_plant_ids,
        completion_outcome: "not_seen_blooming_this_season",
      });
      close();
    });
  } else {
    notSeen.remove();
  }
  confirm.textContent = String(t("tasks.action_complete"));
  confirm.addEventListener("click", () => {
    const completed_plant_ids = [...selected];
    if (completed_plant_ids.length === 0) {
      syncState();
      checkboxes[0]?.focus();
      return;
    }
    onConfirm({ action: "complete", completed_plant_ids, completion_outcome: "done" });
    close();
  });
  syncState();
  checkboxes[0]?.focus();
}
