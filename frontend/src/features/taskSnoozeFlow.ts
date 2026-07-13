import { createModal } from "../components/dialogCore";
import { t } from "../core/i18n";
import { formatLocalDate } from "./taskSnoozePolicy";

export interface TaskDateDialogOptions {
  title: string;
  defaultDate: string;
  onConfirm: (date: string) => void;
  warning?: string | undefined;
  requireManualDate?: boolean | undefined;
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
  if (warning) {
    warningEl.hidden = false;
    warningEl.textContent = warning;
  }
  const input = dialog.querySelector<HTMLInputElement>("input[type='date']")!;
  input.value = requireManualDate ? "" : defaultDate;
  input.min = formatLocalDate(new Date());
  input.required = requireManualDate;
  input.setAttribute("aria-label", title);
  if (warning) input.setAttribute("aria-describedby", warningEl.id);
  const cancelBtn = dialog.querySelector<HTMLButtonElement>(".confirm-no")!;
  cancelBtn.textContent = t("common.cancel") as string;
  cancelBtn.addEventListener("click", close);
  const confirm = (): void => {
    if (!input.value) return;
    onConfirm(input.value);
    close();
  };
  const okBtn = dialog.querySelector<HTMLButtonElement>(".confirm-yes")!;
  okBtn.textContent = t("common.save") as string;
  okBtn.addEventListener("click", confirm);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") confirm();
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
