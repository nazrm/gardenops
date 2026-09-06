import type { GardenTask, TaskType } from "../core/models";
import { t } from "../core/i18n";
import { createModal } from "../components/dialogCore";
import { showToast } from "../components/toast";
import type { TaskActionRequest } from "../services/api";
import { getPlantPlots } from "../services/api";
import { assertOfflineQueueContext, captureOfflineQueueContext, isOnline } from "../services/offlineQueue";

type CompletionTask = Pick<GardenTask, "task_type" | "plant_ids"> &
  Partial<Pick<GardenTask, "plot_ids" | "observation_timezone">>;
type TaskActionLabelTask = Pick<GardenTask, "task_type" | "title">;

interface TaskCompletionDialogOptions {
  modalParent?: HTMLElement | null | undefined;
  plotNames?: Map<string, string>;
  onHistory?: (plantId: string) => void;
  onClose?: () => void;
}

const confirmedPlacements = new Map<string, string[]>();
let placementsContext: ReturnType<typeof captureOfflineQueueContext> | null = null;

const CAPTURE_TASK_TYPES = new Set<TaskType>([
  "observe_bloom",
  "prune",
  "fertilize",
]);

export function needsCompletionSelection(task: CompletionTask): boolean {
  return CAPTURE_TASK_TYPES.has(task.task_type) && (task.plant_ids?.length ?? 0) > 1;
}

export function needsCompletionDialog(task: CompletionTask): boolean {
  return CAPTURE_TASK_TYPES.has(task.task_type);
}

export function canQueueDefaultCompletionOffline(task: CompletionTask): boolean {
  return !needsCompletionDialog(task);
}

export function canQueueCompletionOffline(task: CompletionTask): boolean {
  return canQueueDefaultCompletionOffline(task) || CAPTURE_TASK_TYPES.has(task.task_type);
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
  `, { modalParent: options.modalParent, onClose: options.onClose });
  dialog.querySelector("h3")!.textContent = String(t("tasks.complete_select_plants_title"));
  const list = dialog.querySelector<HTMLElement>(".task-completion-list")!;
  const feedback = dialog.querySelector<HTMLElement>(".task-completion-feedback")!;
  const confirm = dialog.querySelector<HTMLButtonElement>(".confirm-yes")!;
  const selectAll = dialog.querySelector<HTMLButtonElement>(".task-completion-select-all")!;
  const clear = dialog.querySelector<HTMLButtonElement>(".task-completion-clear")!;
  const cancel = dialog.querySelector<HTMLButtonElement>(".confirm-no")!;
  const notSeen = dialog.querySelector<HTMLButtonElement>(".task-completion-not-seen")!;
  const dateLabel = document.createElement("label");
  dateLabel.className = "task-occurrence-date";
  dateLabel.textContent = String(t("tasks.occurred_on"));
  const dateInput = document.createElement("input");
  dateInput.type = "date";
  dateInput.required = true;
  const todayParts = new Intl.DateTimeFormat("en-CA", {
    timeZone: task.observation_timezone || "Europe/Oslo",
    year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date());
  const part = (type: string): string => todayParts.find((p) => p.type === type)?.value ?? "";
  dateInput.value = `${part("year")}-${part("month")}-${part("day")}`;
  dateInput.max = dateInput.value;
  dateLabel.appendChild(dateInput);
  list.before(dateLabel);
  const observedPlots = new Set<string>();
  const locationCheckboxes: HTMLInputElement[] = [];
  const assignmentContext = captureOfflineQueueContext();
  const assignmentCurrent = (): boolean => {
    try { assertOfflineQueueContext(assignmentContext); return dialog.isConnected; }
    catch { return false; }
  };
  try {
    if (!placementsContext) throw new Error("No placement cache");
    assertOfflineQueueContext(placementsContext);
  } catch { confirmedPlacements.clear(); }
  placementsContext = assignmentContext;
  let locationsLoading = false;
  let renderLocations = (): void => {};
  let loadLocations = async (): Promise<void> => {};
  if (task.task_type === "observe_bloom") {
    const locations = document.createElement("fieldset");
    locations.className = "task-observed-locations";
    const legend = document.createElement("legend");
    legend.textContent = String(t("tasks.observed_locations"));
    const hint = document.createElement("p");
    hint.className = "text-muted";
    hint.textContent = String(t("tasks.observed_locations_hint"));
    const choices = document.createElement("div");
    const notice = document.createElement("p");
    notice.setAttribute("role", "status");
    const retry = document.createElement("button");
    retry.type = "button";
    retry.textContent = String(t("common.retry"));
    retry.hidden = true;
    retry.addEventListener("click", () => void loadLocations());
    locations.append(legend, hint, choices, notice, retry);
    renderLocations = () => {
      const ids = [...selected];
      const common = ids.length ? (confirmedPlacements.get(ids[0]!) ?? [])
        .filter((plotId) => ids.every((id) => confirmedPlacements.get(id)?.includes(plotId))) : [];
      observedPlots.clear();
      locationCheckboxes.length = 0;
      choices.replaceChildren();
      for (const plotId of common) {
        const label = document.createElement("label");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.value = plotId;
        checkbox.checked = common.length === 1;
        if (checkbox.checked) observedPlots.add(plotId);
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) observedPlots.add(plotId);
          else observedPlots.delete(plotId);
        });
        label.append(checkbox, document.createTextNode(options.plotNames?.get(plotId) ?? plotId));
        locationCheckboxes.push(checkbox);
        choices.appendChild(label);
      }
    };
    loadLocations = async () => {
      if (!assignmentCurrent() || locationsLoading) return;
      retry.hidden = true;
      if (!isOnline()) {
        notice.textContent = String(t("tasks.locations_cached"));
        renderLocations();
        return;
      }
      locationsLoading = true;
      notice.textContent = String(t("common.loading"));
      syncState();
      try {
        const placements = await Promise.all((task.plant_ids ?? []).map(async (id) =>
          [id, await getPlantPlots(id, { gardenId: assignmentContext.gardenId })] as const));
        if (!assignmentCurrent()) return;
        for (const [id, plots] of placements) confirmedPlacements.set(id, plots);
        notice.textContent = "";
      } catch {
        if (!assignmentCurrent()) return;
        notice.textContent = String(t("tasks.locations_unavailable"));
        retry.hidden = false;
      } finally {
        locationsLoading = false;
        if (assignmentCurrent()) { renderLocations(); syncState(); }
      }
    };
    list.after(locations);
  }
  const closureLabel = document.createElement("label");
  closureLabel.className = "task-season-closure";
  closureLabel.hidden = true;
  const closureConfirmed = document.createElement("input");
  closureConfirmed.type = "checkbox";
  const closureText = document.createElement("span");
  closureLabel.append(closureConfirmed, closureText);
  feedback.before(closureLabel);
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
    confirm.disabled = submitting || selectionMissing || locationsLoading;
    notSeen.disabled = submitting || selectionMissing || locationsLoading;
    selectAll.disabled = submitting;
    clear.disabled = submitting;
    cancel.disabled = submitting;
    dateInput.disabled = submitting;
    closureConfirmed.disabled = submitting;
    for (const checkbox of locationCheckboxes) checkbox.disabled = submitting;
    dialog.toggleAttribute("aria-busy", submitting);
    feedback.textContent = selectionMissing
      ? String(t("tasks.complete_select_one"))
      : submitError;
  };

  const submit = async (
    body: TaskActionRequest,
    submitButton: HTMLButtonElement,
  ): Promise<void> => {
    if (submitting || locationsLoading) return;
    if (!dateInput.reportValidity()) return;
    if (needsCompletionSelection(task) && selected.size === 0) {
      syncState();
      (checkboxes[0] ?? confirm).focus();
      return;
    }
    submitting = true;
    submitError = "";
    syncState();
    try {
      body = {
        ...body,
        occurred_on: dateInput.value,
        ...(task.task_type === "observe_bloom"
          ? { observed_plot_ids: body.completion_outcome === "done" ? [...observedPlots] : [] }
          : {}),
      };
      const result = await onConfirm(body);
      if (result === false) {
        submitError = String(t("tasks.dialog_submit_failed"));
        return;
      }
      close();
      const plantId = body.completed_plant_ids?.[0];
      if (body.completion_outcome === "not_seen_blooming_this_season" && plantId && options.onHistory) {
        showToast(String(t("tasks.season_closed", { year: dateInput.value.slice(0, 4) })), "success", {
          durationMs: 10000,
          actions: [{ label: String(t("tasks.view_history")), onClick: () => options.onHistory?.(plantId) }],
        });
      }
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
      renderLocations();
      closureConfirmed.checked = false;
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
    renderLocations();
    closureConfirmed.checked = false;
    submitError = "";
    syncState();
    confirm.focus();
  });

  clear.textContent = String(t("common.clear"));
  clear.addEventListener("click", () => {
    selected.clear();
    renderLocations();
    closureConfirmed.checked = false;
    submitError = "";
    syncState();
    checkboxes[0]?.focus();
  });

  cancel.textContent = String(t("common.cancel"));
  cancel.addEventListener("click", close);
  if (task.task_type === "observe_bloom") {
    notSeen.textContent = String(t("tasks.action_not_seen_blooming"));
    notSeen.addEventListener("click", () => {
      closureText.textContent = String(t("tasks.close_season_confirm", { year: dateInput.value.slice(0, 4) }));
      if (closureLabel.hidden || !closureConfirmed.checked) {
        closureLabel.hidden = false;
        closureConfirmed.focus();
        return;
      }
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
  dateInput.addEventListener("change", () => {
    closureConfirmed.checked = false;
    closureText.textContent = String(t("tasks.close_season_confirm", { year: dateInput.value.slice(0, 4) }));
  });
  syncState();
  void loadLocations();
  (checkboxes[0] ?? confirm).focus();
}
