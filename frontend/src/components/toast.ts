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

  let remainingMs = options.durationMs ?? TOAST_DURATION;
  let startedAt = Date.now();
  let timeout = window.setTimeout(() => removeToast(toast), remainingMs);
  let paused = false;

  const schedule = (): void => {
    startedAt = Date.now();
    timeout = window.setTimeout(() => removeToast(toast), remainingMs);
  };
  const pause = (): void => {
    if (paused || !toast.isConnected) return;
    paused = true;
    window.clearTimeout(timeout);
    remainingMs = Math.max(0, remainingMs - (Date.now() - startedAt));
  };
  const resume = (): void => {
    if (!paused || !toast.isConnected) return;
    paused = false;
    schedule();
  };

  toast.addEventListener("mouseenter", pause);
  toast.addEventListener("mouseleave", resume);
  toast.addEventListener("focusin", pause);
  toast.addEventListener("focusout", () => {
    if (!toast.contains(document.activeElement)) {
      resume();
    }
  });
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
