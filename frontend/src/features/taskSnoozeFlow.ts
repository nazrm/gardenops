import { confirmDialog, createModal } from "../components/dialogCore";
import { t } from "../core/i18n";
import {
  formatLocalDate,
  type TaskSnoozeDateSafety,
} from "./taskSnoozePolicy";

export interface TaskDateDialogOptions {
  title: string;
  defaultDate: string;
  onConfirm: (date: string, confirmOutsideWindow?: boolean) => void;
  warning?: string | undefined;
  requireManualDate?: boolean | undefined;
  maxDate?: string | undefined;
  getDateSafety?: ((date: string) => TaskSnoozeDateSafety) | undefined;
  modalParent?: HTMLElement | null | undefined;
  onClose?: (() => void) | undefined;
}

export interface TaskSnoozeCorrectionNotice {
  message: string;
  actionLabel: string;
  durationMs: number;
  onChangeDate: () => void;
}

export function openTaskDateDialog({
  title,
  defaultDate,
  onConfirm,
  warning,
  requireManualDate = false,
  maxDate,
  getDateSafety,
  modalParent,
  onClose,
}: TaskDateDialogOptions): void {
  const { dialog, close } = createModal(title, `
    <div class="modal-content confirm-dialog">
      <h3></h3>
      <p class="task-date-dialog-warning" hidden></p>
      <input type="date" class="prompt-dialog-input" />
      <div class="button-row">
        <button type="button" class="confirm-yes"></button>
        <button type="button" class="confirm-no"></button>
      </div>
    </div>
  `, { modalParent, onClose });
  const heading = dialog.querySelector("h3")!;
  heading.textContent = title;
  const warningEl = dialog.querySelector<HTMLElement>(".task-date-dialog-warning")!;
  warningEl.id = "task-date-dialog-warning";
  warningEl.setAttribute("role", "alert");
  const input = dialog.querySelector<HTMLInputElement>("input[type='date']")!;
  input.value = requireManualDate ? "" : defaultDate;
  input.min = formatLocalDate(new Date());
  if (maxDate) input.max = maxDate;
  input.required = true;
  input.setAttribute("aria-label", title);
  if (warning || getDateSafety) input.setAttribute("aria-describedby", warningEl.id);

  const updateDateSafety = (): TaskSnoozeDateSafety | undefined => {
    const safety = input.value ? getDateSafety?.(input.value) : undefined;
    const message = safety?.message ?? warning;
    warningEl.hidden = !message;
    warningEl.textContent = message ?? "";
    return safety;
  };
  updateDateSafety();

  const cancelBtn = dialog.querySelector<HTMLButtonElement>(".confirm-no")!;
  cancelBtn.textContent = t("common.cancel") as string;
  cancelBtn.addEventListener("click", close);
  const okBtn = dialog.querySelector<HTMLButtonElement>(".confirm-yes")!;
  let confirming = false;
  const confirm = async (): Promise<void> => {
    const safety = updateDateSafety();
    if (!input.value || !input.reportValidity() || safety?.blocked) {
      input.focus();
      return;
    }
    if (safety?.confirmationRequired) {
      if (confirming) return;
      confirming = true;
      okBtn.disabled = true;
      const accepted = await confirmDialog(
        safety.message ?? "",
        safety.confirmationLabel,
      );
      confirming = false;
      okBtn.disabled = false;
      if (!accepted) {
        input.focus();
        return;
      }
      onConfirm(input.value, true);
      close();
      return;
    }
    onConfirm(input.value);
    close();
  };
  okBtn.textContent = t("common.save") as string;
  okBtn.addEventListener("click", () => {
    void confirm();
  });
  input.addEventListener("input", updateDateSafety);
  input.addEventListener("change", updateDateSafety);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      void confirm();
    }
  });
  input.focus();
}

export function getTaskSnoozeCorrectionNotice(
  snoozeUntil: string,
  onChangeDate: () => void,
): TaskSnoozeCorrectionNotice {
  return {
    message: t("tasks.snoozed_until_toast", { date: snoozeUntil }) as string,
    actionLabel: t("tasks.snooze_change_date") as string,
    durationMs: 10_000,
    onChangeDate,
  };
}
