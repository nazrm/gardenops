import type { Plant } from "../core/models";
import { t } from "../core/i18n";
import { sanitizeUrl } from "../core/sanitize";
import { formatBloomMonth } from "./dataTables";

type SearchResultPlant = Pick<Plant, "plt_id" | "name" | "latin">;

interface RenderSidebarParams {
  sidebar: HTMLElement;
  plotId: string;
  plants: Plant[];
  onClose: () => void;
  onSearch: (event: Event) => void;
  onRemove: (pltId: string) => void;
}

function renderPlantCard(plant: Plant, onRemove: (pltId: string) => void, plotId?: string): HTMLElement {
  const card = document.createElement("div");
  card.className = "plant-card";

  const header = document.createElement("div");
  header.className = "plant-header";

  const name = document.createElement("strong");
  name.textContent = plant.name;

  const actions = document.createElement("div");
  const safeLink = sanitizeUrl(plant.link ?? "");
  if (safeLink) {
    const link = document.createElement("a");
    link.href = safeLink;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "\uD83D\uDD17";
    actions.appendChild(link);
  }

  if (plotId) {
    const remove = document.createElement("button");
    remove.className = "remove-btn";
    remove.dataset["remove"] = plant.plt_id;
    remove.setAttribute("aria-label", t("sidebar.remove_plant"));
    remove.type = "button";
    remove.textContent = "\u00d7";
    remove.addEventListener("click", () => onRemove(plant.plt_id));
    actions.appendChild(remove);
  }

  header.append(name, actions);
  card.appendChild(header);

  if (plant.latin) {
    const latin = document.createElement("div");
    latin.className = "plant-latin";
    const em = document.createElement("em");
    em.textContent = plant.latin;
    latin.appendChild(em);
    card.appendChild(latin);
  }

  const details = [
    plant.height_cm ? `${plant.height_cm} cm` : "",
    plant.bloom_month ? t("sidebar.bloom", { value: formatBloomMonth(plant.bloom_month) }) : "",
    plant.color ? t("sidebar.color", { value: plant.color }) : "",
    plant.light ? t("sidebar.light", { value: plant.light }) : "",
    plant.quantity ? t("sidebar.qty", { value: plant.quantity }) : "",
  ].filter(Boolean).join(" • ");

  const detail = document.createElement("div");
  detail.className = "plant-details";
  detail.textContent = details;
  card.appendChild(detail);

  return card;
}

export function renderSidebarContent(params: RenderSidebarParams): void {
  const { sidebar, plotId, plants, onClose, onSearch, onRemove } = params;

  const header = document.createElement("div");
  header.className = "sidebar-header";

  const title = document.createElement("h2");
  title.textContent = plotId;

  const closeBtn = document.createElement("button");
  closeBtn.className = "close-btn";
  closeBtn.id = "close-sidebar-btn";
  closeBtn.type = "button";
  closeBtn.textContent = "\u00d7";
  closeBtn.addEventListener("click", onClose);

  header.append(title, closeBtn);

  const addPlantSection = document.createElement("div");
  addPlantSection.className = "add-plant-section";

  const searchInput = document.createElement("input");
  searchInput.type = "text";
  searchInput.id = "plant-search";
  searchInput.placeholder = t("sidebar.search_placeholder");
  searchInput.addEventListener("input", onSearch);

  const searchResults = document.createElement("div");
  searchResults.id = "search-results";
  searchResults.className = "search-results";

  addPlantSection.append(searchInput, searchResults);

  const plantList = document.createElement("div");
  plantList.className = "plant-list";
  if (plants.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-message";
    empty.textContent = t("sidebar.no_plants");
    plantList.appendChild(empty);
  } else {
    plantList.append(...plants.map((plant) => renderPlantCard(plant, onRemove, plotId)));
  }

  sidebar.replaceChildren(header, addPlantSection, plantList);
}

export function renderSearchResults(
  resultsDiv: HTMLElement,
  plants: SearchResultPlant[],
  onAdd: (pltId: string) => void,
): void {
  if (plants.length === 0) {
    const empty = document.createElement("p");
    empty.className = "no-results";
    empty.textContent = t("sidebar.no_results");
    resultsDiv.replaceChildren(empty);
    return;
  }

  const results = plants.slice(0, 10).map((plant) => {
    const button = document.createElement("button");
    button.className = "search-result";
    button.dataset["addPlant"] = plant.plt_id;
    button.type = "button";

    const name = document.createElement("strong");
    name.textContent = plant.name;
    button.appendChild(name);

    if (plant.latin) {
      button.append(document.createTextNode(" "));
      const latin = document.createElement("em");
      latin.textContent = plant.latin;
      button.appendChild(latin);
    }

    button.addEventListener("click", () => onAdd(plant.plt_id));
    return button;
  });

  resultsDiv.replaceChildren(...results);
}

export function clearSidebarContent(sidebar: HTMLElement): void {
  const empty = document.createElement("div");
  empty.className = "sidebar-empty";

  const text = document.createElement("p");
  text.textContent = t("sidebar.click_plot_hint");
  empty.appendChild(text);

  sidebar.replaceChildren(empty);
}
