import type { AppTab } from "../core/models";

export function wireTopTabs(onActivate: (tab: AppTab) => void): void {
  const tabs = Array.from(document.querySelectorAll<HTMLButtonElement>(".top-tab"));

  tabs.forEach((btn) => {
    btn.addEventListener("click", () => {
      const tab = btn.dataset["tab"] as AppTab | undefined;
      if (!tab) return;
      onActivate(tab);
    });
  });
}
