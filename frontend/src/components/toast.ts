type ToastType = "success" | "error";

const TOAST_DURATION = 3000;

export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface ToastOptions {
  actions?: ToastAction[];
  durationMs?: number;
}

export function showToast(
  message: string,
  type: ToastType = "success",
  options: ToastOptions = {},
): void {
  const container = getOrCreateContainer();

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.setAttribute("role", "status");

  const text = document.createElement("span");
  text.textContent = message;
  toast.appendChild(text);

  for (const action of options.actions ?? []) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "toast-action";
    button.textContent = action.label;
    button.addEventListener("click", () => {
      action.onClick();
      removeToast(toast);
    });
    toast.appendChild(button);
  }

  container.appendChild(toast);

  requestAnimationFrame(() => toast.classList.add("toast-visible"));

  const timeout = window.setTimeout(() => removeToast(toast), options.durationMs ?? TOAST_DURATION);
  toast.addEventListener("mouseenter", () => window.clearTimeout(timeout), { once: true });
  toast.addEventListener("focusin", () => window.clearTimeout(timeout), { once: true });
}

function getOrCreateContainer(): HTMLElement {
  let container = document.getElementById("toast-container");
  if (!container) {
    container = document.createElement("div");
    container.id = "toast-container";
    document.body.appendChild(container);
  }
  return container;
}

function removeToast(toast: HTMLElement): void {
  toast.classList.remove("toast-visible");
  toast.addEventListener("transitionend", () => toast.remove(), { once: true });
  window.setTimeout(() => toast.remove(), 500);
}
