import type { AppTab } from "../core/models";

const ORDER: AppTab[] = ["map", "garden", "activity", "insights", "admin"];

export function wireTopTabs(onActivate: (tab: AppTab) => void): void {
  const tabs = Array.from(document.querySelectorAll<HTMLButtonElement>(".top-tab"));

  tabs.forEach((btn) => {
    btn.addEventListener("click", () => {
      const tab = btn.dataset["tab"] as AppTab | undefined;
      if (!tab) return;
      onActivate(tab);
    });

    btn.addEventListener("keydown", (e) => {
      const current = btn.dataset["tab"] as AppTab | undefined;
      if (!current) return;

      const idx = ORDER.indexOf(current);
      const first = tabs[0];
      const last = tabs[tabs.length - 1];

      if (e.key === "ArrowRight") {
        e.preventDefault();
        const next = tabs[(idx + 1) % tabs.length];
        next?.focus();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        const prev = tabs[(idx - 1 + tabs.length) % tabs.length];
        prev?.focus();
      } else if (e.key === "Home") {
        e.preventDefault();
        first?.focus();
      } else if (e.key === "End") {
        e.preventDefault();
        last?.focus();
      } else if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onActivate(current);
      }
    });
  });
}
