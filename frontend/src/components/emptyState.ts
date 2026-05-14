export interface EmptyStateOptions {
  icon: string;
  headline: string;
  hint?: string | undefined;
  ctaLabel?: string | undefined;
  ctaAction?: (() => void) | undefined;
}

export function renderEmptyState(
  container: HTMLElement,
  options: EmptyStateOptions,
): void {
  container.replaceChildren();
  const wrapper = document.createElement("div");
  wrapper.className = "empty-state";

  const iconEl = document.createElement("div");
  iconEl.className = "empty-state__icon";
  iconEl.textContent = options.icon;
  iconEl.setAttribute("aria-hidden", "true");
  wrapper.appendChild(iconEl);

  const headline = document.createElement("p");
  headline.className = "empty-state__headline";
  headline.textContent = options.headline;
  wrapper.appendChild(headline);

  if (options.hint) {
    const hint = document.createElement("p");
    hint.className = "empty-state__hint";
    hint.textContent = options.hint;
    wrapper.appendChild(hint);
  }

  if (options.ctaLabel && options.ctaAction) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "empty-state__cta btn-primary";
    btn.textContent = options.ctaLabel;
    btn.addEventListener("click", options.ctaAction);
    wrapper.appendChild(btn);
  }

  container.appendChild(wrapper);
}
