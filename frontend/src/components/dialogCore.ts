import { t } from "../core/i18n";
import { setReviewedDynamicHtml } from "../core/sanitize";

interface ModalStackEntry {
  dialog: HTMLElement;
  parent: HTMLElement | null;
  parentAriaHidden: string | null;
  parentWasInert: boolean;
  returnFocus: HTMLElement | null;
}

export interface ModalOptions {
  modalParent?: HTMLElement | null | undefined;
  onClose?: (() => void) | undefined;
}

const modalStack: ModalStackEntry[] = [];

function activeModal(): ModalStackEntry | undefined {
  return modalStack.at(-1);
}

function pushModal(dialog: HTMLElement, modalParent?: HTMLElement | null): ModalStackEntry {
  const parent = modalParent ?? activeModal()?.dialog ?? null;
  const entry: ModalStackEntry = {
    dialog,
    parent,
    parentAriaHidden: parent?.getAttribute("aria-hidden") ?? null,
    parentWasInert: parent?.hasAttribute("inert") ?? false,
    returnFocus: document.activeElement instanceof HTMLElement ? document.activeElement : null,
  };
  if (parent) {
    parent.setAttribute("aria-hidden", "true");
    parent.setAttribute("inert", "");
  }
  modalStack.push(entry);
  return entry;
}

function popModal(entry: ModalStackEntry): void {
  const index = modalStack.indexOf(entry);
  if (index === -1) return;
  modalStack.splice(index, 1);

  if (entry.parent && !modalStack.some((candidate) => candidate.parent === entry.parent)) {
    if (entry.parentAriaHidden === null) {
      entry.parent.removeAttribute("aria-hidden");
    } else {
      entry.parent.setAttribute("aria-hidden", entry.parentAriaHidden);
    }
    if (entry.parentWasInert) {
      entry.parent.setAttribute("inert", "");
    } else {
      entry.parent.removeAttribute("inert");
    }
  }

  if (entry.returnFocus?.isConnected) {
    entry.returnFocus.focus();
  }
}

export function trapFocus(container: HTMLElement): () => void {
  const selector = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';
  const handler = (e: KeyboardEvent) => {
    if (e.key !== "Tab") return;
    const focusable = Array.from(
      container.querySelectorAll<HTMLElement>(selector),
    ).filter((el) => !el.hasAttribute("disabled"));
    if (focusable.length === 0) return;
    const first = focusable[0]!;
    const last = focusable[focusable.length - 1]!;
    if (e.shiftKey && document.activeElement === first) {
      last.focus();
      e.preventDefault();
    } else if (!e.shiftKey && document.activeElement === last) {
      first.focus();
      e.preventDefault();
    }
  };
  container.addEventListener("keydown", handler);
  return () => container.removeEventListener("keydown", handler);
}

export function createModal(ariaLabel: string, innerMarkup: string, options: ModalOptions = {}): {
  dialog: HTMLDivElement;
  close: () => void;
} {
  const dialog = document.createElement("div");
  dialog.className = "modal";
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-label", ariaLabel);
  setReviewedDynamicHtml(dialog, innerMarkup);
  document.body.appendChild(dialog);

  const modalEntry = pushModal(dialog, options.modalParent);
  const releaseFocusTrap = trapFocus(dialog);
  let closed = false;

  const onEscape = (e: KeyboardEvent) => {
    if (e.key === "Escape" && activeModal()?.dialog === dialog) {
      e.preventDefault();
      close();
    }
  };
  const close = () => {
    if (closed) return;
    closed = true;
    releaseFocusTrap();
    dialog.remove();
    window.removeEventListener("keydown", onEscape);
    popModal(modalEntry);
    options.onClose?.();
  };
  window.addEventListener("keydown", onEscape);

  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "close-btn modal-close-btn";
  closeBtn.setAttribute("aria-label", ariaLabel ? `Close ${ariaLabel}` : "Close");
  closeBtn.textContent = "×";
  closeBtn.addEventListener("click", close);
  const content = dialog.querySelector(".modal-content");
  if (content) {
    content.insertBefore(closeBtn, content.firstChild);
  } else {
    dialog.insertBefore(closeBtn, dialog.firstChild);
  }

  const firstFocusable = dialog.querySelector<HTMLElement>(
    'input, select, textarea, ' +
    'button:not(.modal-close-btn), [href], ' +
    '[tabindex]:not([tabindex="-1"])',
  );
  firstFocusable?.focus();

  return { dialog, close };
}

export function confirmDialog(
  message: string,
  confirmLabel?: string,
): Promise<boolean> {
  return new Promise((resolve) => {
    const dialog = document.createElement("div");
    dialog.className = "modal";
    dialog.setAttribute("role", "alertdialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-label", t("common.ok"));

    const content = document.createElement("div");
    content.className = "modal-content confirm-dialog";

    const text = document.createElement("p");
    text.textContent = message;

    const actions = document.createElement("div");
    actions.className = "button-row";

    const confirmBtn = document.createElement("button");
    confirmBtn.type = "button";
    confirmBtn.className = "confirm-yes";
    confirmBtn.textContent = confirmLabel ?? t("common.ok");

    const cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "confirm-no";
    cancelBtn.textContent = t("common.cancel");

    actions.append(confirmBtn, cancelBtn);
    content.append(text, actions);
    dialog.appendChild(content);
    document.body.appendChild(dialog);

    const modalEntry = pushModal(dialog);
    const removeTrap = trapFocus(dialog);
    let closed = false;
    const close = (result: boolean) => {
      if (closed) return;
      closed = true;
      removeTrap();
      window.removeEventListener("keydown", onKey);
      dialog.remove();
      popModal(modalEntry);
      resolve(result);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && activeModal()?.dialog === dialog) {
        e.preventDefault();
        close(false);
      }
    };
    window.addEventListener("keydown", onKey);

    confirmBtn.addEventListener("click", () => close(true));
    cancelBtn.addEventListener("click", () => close(false));
    confirmBtn.focus();
  });
}

export function promptDialog(
  message: string,
  defaultValue?: string,
): Promise<string | null> {
  return new Promise((resolve) => {
    const { dialog, close } = createModal(t("common.ok"), `
      <div class="modal-content confirm-dialog">
        <p></p>
        <input type="text" class="prompt-dialog-input" />
        <div class="button-row">
          <button type="button" class="confirm-yes">${t("common.ok")}</button>
          <button type="button" class="confirm-no">${t("common.cancel")}</button>
        </div>
      </div>
    `);
    dialog.querySelector("p")!.textContent = message;
    const input = dialog.querySelector<HTMLInputElement>(".prompt-dialog-input")!;
    input.value = defaultValue ?? "";
    input.focus();
    input.select();
    const finish = (value: string | null) => { close(); resolve(value); };
    dialog.querySelector(".confirm-yes")!.addEventListener("click", () => finish(input.value));
    dialog.querySelector(".confirm-no")!.addEventListener("click", () => finish(null));
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") finish(input.value); });
  });
}

export function promptPasswordDialog(message: string): Promise<string | null> {
  return new Promise((resolve) => {
    const { dialog, close } = createModal(t("common.ok"), `
      <div class="modal-content confirm-dialog">
        <p></p>
        <input type="password" class="prompt-dialog-input" autocomplete="current-password" />
        <div class="button-row">
          <button type="button" class="confirm-yes">${t("common.ok")}</button>
          <button type="button" class="confirm-no">${t("common.cancel")}</button>
        </div>
      </div>
    `);
    dialog.querySelector("p")!.textContent = message;
    const input = dialog.querySelector<HTMLInputElement>(".prompt-dialog-input")!;
    input.focus();
    const finish = (value: string | null) => { close(); resolve(value); };
    dialog.querySelector(".confirm-yes")!.addEventListener("click", () => finish(input.value));
    dialog.querySelector(".confirm-no")!.addEventListener("click", () => finish(null));
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") finish(input.value); });
  });
}
