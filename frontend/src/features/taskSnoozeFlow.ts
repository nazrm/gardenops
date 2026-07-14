import { confirmDialog, createModal } from "../components/dialogCore";
import { t } from "../core/i18n";
import {
  formatLocalDate,
  type TaskSnoozeDateSafety,
} from "./taskSnoozePolicy";

export interface TaskDateDialogOptions {
  title: string;
  defaultDate: string;
  onConfirm: (
    date: string,
    confirmOutsideWindow?: boolean,
  ) => boolean | void | Promise<boolean | void>;
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
      <p class="task-date-dialog-feedback" role="alert" aria-live="assertive" hidden></p>
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
  const feedback = dialog.querySelector<HTMLElement>(".task-date-dialog-feedback")!;
  feedback.id = "task-date-dialog-feedback";
  input.value = requireManualDate ? "" : defaultDate;
  input.min = formatLocalDate(new Date());
  if (maxDate) input.max = maxDate;
  input.required = true;
  input.setAttribute("aria-label", title);
  input.setAttribute("aria-describedby", `${warningEl.id} ${feedback.id}`);

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
  let submitting = false;
  const setSubmitting = (pending: boolean): void => {
    submitting = pending;
    input.disabled = pending;
    okBtn.disabled = pending;
    cancelBtn.disabled = pending;
    dialog.toggleAttribute("aria-busy", pending);
  };
  const showSubmitFailure = (): void => {
    feedback.hidden = false;
    feedback.textContent = String(t("tasks.dialog_submit_failed"));
  };
  const confirm = async (): Promise<void> => {
    if (submitting) return;
    let focusInputAfterSubmit = false;
    feedback.hidden = true;
    feedback.textContent = "";
    const safety = updateDateSafety();
    if (!input.value || !input.reportValidity() || safety?.blocked) {
      input.focus();
      return;
    }
    setSubmitting(true);
    try {
      let confirmOutsideWindow = false;
      if (safety?.confirmationRequired) {
        const accepted = await confirmDialog(
          safety.message ?? "",
          safety.confirmationLabel,
        );
        if (!accepted) {
          focusInputAfterSubmit = true;
          return;
        }
        confirmOutsideWindow = true;
      }
      const result = await onConfirm(
        input.value,
        confirmOutsideWindow || undefined,
      );
      if (result === false) {
        showSubmitFailure();
        return;
      }
      close();
    } catch {
      showSubmitFailure();
    } finally {
      if (dialog.isConnected) {
        setSubmitting(false);
        if (focusInputAfterSubmit) input.focus();
        else if (!feedback.hidden) okBtn.focus();
      }
    }
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
