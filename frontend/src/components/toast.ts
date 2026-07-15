type ToastType = "success" | "error";

const TOAST_DURATION = 3000;
const MAX_VISIBLE_TOASTS = 3;

interface ToastTimerState {
  paused: boolean;
  removing: boolean;
  remainingMs: number;
  startedAt: number;
  timeout: number;
}

const activeToasts = new Map<string, HTMLElement>();
const toastTimerStates = new WeakMap<HTMLElement, ToastTimerState>();
let toastSequence = 0;

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
  const deduplicate = (options.actions?.length ?? 0) === 0;
  const toastKey = deduplicate
    ? `${type}:${message}`
    : `${type}:${message}:${++toastSequence}`;
  const existing = deduplicate ? activeToasts.get(toastKey) : undefined;
  if (existing?.isConnected && existing.dataset["toastClosing"] !== "true") {
    resetToastTimer(existing, options.durationMs ?? TOAST_DURATION);
    existing.classList.add("toast-visible");
    return;
  }
  activeToasts.delete(toastKey);

  const visibleToasts = Array.from(
    container.querySelectorAll<HTMLElement>(".toast"),
  ).filter((toast) => toast.dataset["toastClosing"] !== "true");
  while (visibleToasts.length >= MAX_VISIBLE_TOASTS) {
    const oldest = visibleToasts.shift();
    if (oldest) removeToast(oldest, true);
  }

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  if ((options.actions?.length ?? 0) > 0) toast.classList.add("toast-interactive");
  toast.dataset["toastKey"] = toastKey;
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
  activeToasts.set(toastKey, toast);

  requestAnimationFrame(() => toast.classList.add("toast-visible"));

  resetToastTimer(toast, options.durationMs ?? TOAST_DURATION);
  const pause = (): void => {
    const state = toastTimerStates.get(toast);
    if (!state || state.paused || state.removing || !toast.isConnected) return;
    state.paused = true;
    window.clearTimeout(state.timeout);
    state.remainingMs = Math.max(0, state.remainingMs - (Date.now() - state.startedAt));
  };
  const resume = (): void => {
    const state = toastTimerStates.get(toast);
    if (!state || !state.paused || state.removing || !toast.isConnected) return;
    state.paused = false;
    scheduleToastRemoval(toast, state);
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

function resetToastTimer(toast: HTMLElement, durationMs: number): void {
  const prior = toastTimerStates.get(toast);
  if (prior) window.clearTimeout(prior.timeout);
  const state: ToastTimerState = {
    paused: false,
    removing: false,
    remainingMs: durationMs,
    startedAt: Date.now(),
    timeout: 0,
  };
  toastTimerStates.set(toast, state);
  scheduleToastRemoval(toast, state);
}

function scheduleToastRemoval(toast: HTMLElement, state: ToastTimerState): void {
  state.startedAt = Date.now();
  state.timeout = window.setTimeout(() => removeToast(toast), state.remainingMs);
}

function removeToast(toast: HTMLElement, immediately = false): void {
  const state = toastTimerStates.get(toast);
  if (state?.removing) return;
  if (state) {
    state.removing = true;
    window.clearTimeout(state.timeout);
  }
  toast.dataset["toastClosing"] = "true";
  const toastKey = toast.dataset["toastKey"];
  if (toastKey && activeToasts.get(toastKey) === toast) {
    activeToasts.delete(toastKey);
  }
  toast.classList.remove("toast-visible");
  if (immediately) {
    toast.remove();
    return;
  }
  toast.addEventListener("transitionend", () => toast.remove(), { once: true });
  window.setTimeout(() => toast.remove(), 500);
}
