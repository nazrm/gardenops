import type { AppState } from "../core/models";
import { t } from "../core/i18n";
import {
  getActiveGardenContext,
  getApiErrorMessage,
  getPlantPlots,
  searchPlantsApi,
} from "../services/api";

let searchTimer: number | null = null;
let searchSeq = 0;
let highlightSeq = 0;

let cachedSearchInputs: HTMLInputElement[] | null = null;
let cachedSearchDropdowns: HTMLElement[] | null = null;

function getSearchInputs(): HTMLInputElement[] {
  if (!cachedSearchInputs) {
    cachedSearchInputs = Array.from(
      document.querySelectorAll<HTMLInputElement>(".global-search-input"),
    );
  }
  return cachedSearchInputs;
}

function getSearchDropdowns(): HTMLElement[] {
  if (!cachedSearchDropdowns) {
    cachedSearchDropdowns = Array.from(
      document.querySelectorAll<HTMLElement>(".global-search-dropdown"),
    );
  }
  return cachedSearchDropdowns;
}

export function invalidateSearchCache(): void {
  cachedSearchInputs = null;
  cachedSearchDropdowns = null;
}

function getDropdownForInput(input: HTMLInputElement | null): HTMLElement | null {
  const dropdownId = input?.dataset["dropdownId"];
  if (!dropdownId) return null;
  return document.getElementById(dropdownId);
}

function syncSearchInputs(value: string, source?: HTMLInputElement | null): void {
  getSearchInputs().forEach((input) => {
    if (input !== source) input.value = value;
  });
}

function hideOtherDropdowns(active: HTMLElement | null): void {
  getSearchDropdowns().forEach((dropdown) => {
    if (dropdown !== active) dropdown.hidden = true;
  });
}

export function hideGlobalSearchDropdowns(): void {
  getSearchDropdowns().forEach((dropdown) => {
    dropdown.hidden = true;
  });
}

export function resetGlobalSearchForGardenSwitch(): void {
  searchSeq += 1;
  highlightSeq += 1;
  if (searchTimer !== null) {
    window.clearTimeout(searchTimer);
    searchTimer = null;
  }
  syncSearchInputs("");
  getSearchDropdowns().forEach((dropdown) => {
    dropdown.replaceChildren();
    dropdown.hidden = true;
  });
  document.getElementById("highlight-badge")?.remove();
}

export function handleGlobalSearch(
  state: AppState,
  renderPlots: () => void,
  sourceInput?: HTMLInputElement | null,
  onSelect?: () => void,
): void {
  const input = sourceInput ?? getSearchInputs()[0] ?? null;
  const dropdown = getDropdownForInput(input);
  if (!input || !dropdown) return;

  const query = input.value.trim();
  syncSearchInputs(input.value, input);
  if (query.length < 2) {
    dropdown.hidden = true;
    dropdown.replaceChildren();
    return;
  }

  if (searchTimer !== null) {
    window.clearTimeout(searchTimer);
  }
  const seq = ++searchSeq;
  const gardenId = getActiveGardenContext();
  if (gardenId === null) return;
  searchTimer = window.setTimeout(() => {
    void (async () => {
      let results;
      try {
        results = await searchPlantsApi(query, { limit: 8, gardenId });
      } catch (err) {
        if (
          seq !== searchSeq
          || gardenId !== getActiveGardenContext()
          || input.value.trim() !== query
        ) return;
        const error = document.createElement("div");
        error.className = "dropdown-empty";
        error.textContent = getApiErrorMessage(err);
        dropdown.replaceChildren(error);
        dropdown.hidden = false;
        return;
      }
      if (
        seq !== searchSeq
        || gardenId !== getActiveGardenContext()
        || input.value.trim() !== query
      ) return;
      hideOtherDropdowns(dropdown);
      if (results.length === 0) {
        const empty = document.createElement("div");
        empty.className = "dropdown-empty";
        empty.textContent = t("sidebar.no_results");
        dropdown.replaceChildren(empty);
        dropdown.hidden = false;
        return;
      }

      dropdown.setAttribute("role", "listbox");
      const items = results.slice(0, 8).map((plant, index) => {
        const button = document.createElement("button");
        button.className = `dropdown-item${index === 0 ? " focused" : ""}`;
        button.setAttribute("role", "option");
        button.id = `search-result-${index}`;
        button.dataset["pltId"] = plant.plt_id;

        const name = document.createElement("span");
        name.className = "dropdown-name";
        name.textContent = plant.name;
        button.appendChild(name);

        if (plant.latin) {
          const latin = document.createElement("span");
          latin.className = "dropdown-latin";
          latin.textContent = plant.latin;
          button.appendChild(latin);
        }
        return button;
      });
      dropdown.replaceChildren(...items);
      dropdown.hidden = false;

      dropdown
        .querySelectorAll<HTMLButtonElement>(".dropdown-item")
        .forEach((btn) => {
          btn.addEventListener("click", () => {
            const pltId = btn.dataset["pltId"];
            if (!pltId) return;
            const name =
              btn.querySelector(".dropdown-name")?.textContent ??
              "";
            onSelect?.();
            void highlightPlantPlots(state, pltId, name, renderPlots);
            hideGlobalSearchDropdowns();
          });
        });
    })();
  }, 200);
}

export function handleSearchKeydown(
  state: AppState,
  e: KeyboardEvent,
  renderPlots: () => void,
  sourceInput?: HTMLInputElement | null,
): void {
  const dropdown = getDropdownForInput(
    sourceInput ?? getSearchInputs()[0] ?? null,
  );
  if (!dropdown || dropdown.hidden) return;

  const items =
    dropdown.querySelectorAll<HTMLButtonElement>(".dropdown-item");
  if (items.length === 0) return;

  const focusedIdx = Array.from(items).findIndex((el) =>
    el.classList.contains("focused"),
  );

  if (e.key === "ArrowDown") {
    e.preventDefault();
    const next = Math.min(focusedIdx + 1, items.length - 1);
    items.forEach((el) => el.classList.remove("focused"));
    items[next]?.classList.add("focused");
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    const prev = Math.max(focusedIdx - 1, 0);
    items.forEach((el) => el.classList.remove("focused"));
    items[prev]?.classList.add("focused");
  } else if (e.key === "Enter") {
    e.preventDefault();
    const focused = items[focusedIdx >= 0 ? focusedIdx : 0];
    focused?.click();
  } else if (e.key === "Escape") {
    hideGlobalSearchDropdowns();
    state.highlightedPlotIds.clear();
    state.highlightedPlantName = "";
    renderPlots();
  }
}

export async function highlightPlantPlots(
  state: AppState,
  pltId: string,
  name: string,
  renderPlots: () => void,
): Promise<void> {
  const gardenId = getActiveGardenContext();
  if (gardenId === null) return;
  const seq = ++highlightSeq;
  const plotIds = await getPlantPlots(pltId, { gardenId });
  if (seq !== highlightSeq || gardenId !== getActiveGardenContext()) return;
  state.highlightedPlotIds = new Set(plotIds);
  state.highlightedPlantName = name;

  syncSearchInputs(name);

  renderPlots();

  const badge = document.getElementById("highlight-badge");
  const badgeHost = document.getElementById("map-status-slot")
    ?? document.querySelector<HTMLElement>(".toolbar");
  const renderBadge = (target: HTMLElement) => {
    target.replaceChildren();
    target.append(document.createTextNode(`${name}: `));

    const count = document.createElement("strong");
    count.textContent = String(plotIds.length);
    target.append(count);
    target.append(document.createTextNode(` ${t("popover.plots_suffix")} `));

    const clearBtn = document.createElement("button");
    clearBtn.id = "clear-highlight-btn";
    clearBtn.setAttribute("aria-label", "Clear highlight");
    clearBtn.type = "button";
    clearBtn.textContent = "\u00d7";
    clearBtn.addEventListener("click", () => clearHighlight(state, renderPlots));
    target.append(clearBtn);
  };

  if (!badge && state.highlightedPlotIds.size > 0) {
    if (badgeHost) {
      const span = document.createElement("span");
      span.id = "highlight-badge";
      span.className = "highlight-badge";
      renderBadge(span);
      badgeHost.appendChild(span);
    }
  } else if (badge) {
    if (badgeHost && badge.parentElement !== badgeHost) {
      badgeHost.appendChild(badge);
    }
    renderBadge(badge);
  }
}

export function clearHighlight(
  state: AppState,
  renderPlots: () => void,
): void {
  state.highlightedPlotIds.clear();
  state.highlightedPlantName = "";
  syncSearchInputs("");
  const badge = document.getElementById("highlight-badge");
  badge?.remove();
  hideGlobalSearchDropdowns();
  renderPlots();
}
