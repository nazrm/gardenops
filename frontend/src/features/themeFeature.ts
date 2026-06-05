import { t } from "../core/i18n";

export function initThemeFeature(): void {
  getThemeButtons().forEach((btn) => {
    btn.addEventListener("click", toggleTheme);
  });
  applyStoredTheme();
}

function getThemeButtons(): HTMLButtonElement[] {
  return Array.from(
    document.querySelectorAll<HTMLButtonElement>(
      "[data-theme-toggle]",
    ),
  );
}

function toggleTheme(): void {
  const html = document.documentElement;
  const isDark = html.dataset["theme"] === "dark";
  const next = isDark ? "light" : "dark";
  html.dataset["theme"] = next;
  localStorage.setItem("gardenops-theme", next);
  updateThemeIcon();
}

function applyStoredTheme(): void {
  const stored = localStorage.getItem("gardenops-theme");
  const prefersDark = window.matchMedia(
    "(prefers-color-scheme: dark)",
  ).matches;
  const theme =
    stored ?? (prefersDark ? "dark" : "light");
  document.documentElement.dataset["theme"] = theme;
  updateThemeIcon();
}

export function updateThemeIcon(): void {
  const isDark =
    document.documentElement.dataset["theme"] ===
    "dark";
  const desktopBtn = document.getElementById(
    "theme-toggle",
  );
  if (desktopBtn) {
    desktopBtn.textContent = isDark
      ? "\u2600\uFE0F"
      : "\uD83C\uDF19";
  }
  const mobileBtn = document.getElementById(
    "mobile-theme-toggle",
  );
  if (mobileBtn) {
    mobileBtn.textContent = isDark
      ? t("common.light_mode")
      : t("common.dark_mode");
  }
}
