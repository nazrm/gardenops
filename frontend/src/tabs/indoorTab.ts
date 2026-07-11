import { t } from "../core/i18n.js";
import type { IndoorPlant } from "../core/models.js";
import { renderPlantCard } from "../components/plantCard.js";
import {
  getActiveGardenContext,
  getPlotPlants,
  getApiErrorMessage,
  removePlantFromPlotApi,
  updatePlotPlant,
} from "../services/api.js";
import { showToast } from "../components/toast.js";

// ── Module state ──────────────────────────────────
let indoorPlotId = "";
let indoorGardenId: number | null = null;
let indoorPlants: IndoorPlant[] = [];
let roomLabels: string[] = [];
let sortBy: "name" | "room" | "category" | "quantity" = "name";
let searchQuery = "";
let indoorRequestVersion = 0;

// ── Public API ────────────────────────────────────
export function setIndoorPlotId(
  plotId: string,
  gardenId: number | null = getActiveGardenContext(),
): void {
  const contextChanged = indoorGardenId !== gardenId || indoorPlotId !== plotId;
  if (contextChanged) {
    indoorRequestVersion += 1;
    indoorPlants = [];
    roomLabels = [];
    searchQuery = "";
  }
  indoorGardenId = gardenId;
  indoorPlotId = plotId;
}

export function getIndoorPlotId(): string {
  return indoorPlotId;
}

export function getRoomLabelsList(): string[] {
  return roomLabels;
}

export function resetIndoorState(): void {
  indoorRequestVersion += 1;
  indoorGardenId = null;
  indoorPlotId = "";
  indoorPlants = [];
  roomLabels = [];
  searchQuery = "";
}

function isCurrentIndoorContext(
  gardenId: number | null,
  plotId: string,
): boolean {
  return (
    gardenId !== null
    && gardenId === indoorGardenId
    && plotId === indoorPlotId
    && gardenId === getActiveGardenContext()
  );
}

function isCurrentIndoorRequest(
  gardenId: number | null,
  plotId: string,
  requestVersion: number,
): boolean {
  return (
    requestVersion === indoorRequestVersion
    && isCurrentIndoorContext(gardenId, plotId)
  );
}

function deriveRoomLabels(plants: IndoorPlant[]): string[] {
  return Array.from(
    new Set(
      plants
        .map((plant) => plant.room_label?.trim() ?? "")
        .filter((label) => label.length > 0),
    ),
  ).sort((a, b) => a.localeCompare(b));
}

export async function loadIndoorPlants(): Promise<boolean> {
  const requestGardenId = indoorGardenId;
  const requestPlotId = indoorPlotId;
  const requestVersion = ++indoorRequestVersion;
  if (!requestPlotId || !isCurrentIndoorContext(requestGardenId, requestPlotId)) {
    return false;
  }
  const plants = await (getPlotPlants(requestPlotId) as Promise<
    IndoorPlant[]
  >);
  if (!isCurrentIndoorRequest(requestGardenId, requestPlotId, requestVersion)) {
    return false;
  }
  indoorPlants = plants;
  roomLabels = deriveRoomLabels(plants);
  return true;
}

// ── Sorting & filtering ──────────────────────────
function filteredAndSorted(): IndoorPlant[] {
  let result = indoorPlants;
  if (searchQuery) {
    const q = searchQuery.toLowerCase();
    result = result.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        (p.latin ?? "").toLowerCase().includes(q) ||
        (p.room_label ?? "").toLowerCase().includes(q),
    );
  }
  return [...result].sort((a, b) => {
    switch (sortBy) {
      case "name":
        return a.name.localeCompare(b.name);
      case "room":
        return (a.room_label ?? "").localeCompare(b.room_label ?? "");
      case "category":
        return a.category.localeCompare(b.category);
      case "quantity":
        return b.quantity - a.quantity;
    }
  });
}

function groupByRoom(plants: IndoorPlant[]): Map<string, IndoorPlant[]> {
  const groups = new Map<string, IndoorPlant[]>();
  for (const p of plants) {
    const key = p.room_label || t("indoor.unassigned");
    const list = groups.get(key) ?? [];
    list.push(p);
    groups.set(key, list);
  }
  return groups;
}

// ── Callbacks ─────────────────────────────────────
let onAddPlant: ((container: HTMLElement) => void) | null = null;
let onEditPlant: ((plant: IndoorPlant) => void) | null = null;

export interface IndoorRenderOptions {
  canWrite?: boolean;
}

export function setOnAddPlant(cb: (container: HTMLElement) => void): void {
  onAddPlant = cb;
}

export function setOnEditPlant(cb: (plant: IndoorPlant) => void): void {
  onEditPlant = cb;
}

// ── Render ────────────────────────────────────────

export function renderIndoorPlants(
  container: HTMLElement,
  { canWrite = false }: IndoorRenderOptions = {},
): void {
  const renderGardenId = indoorGardenId;
  const renderPlotId = indoorPlotId;
  container.textContent = "";

  if (canWrite) {
    // Add plant button
    const addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.className = "btn btn-primary";
    addBtn.textContent = t("indoor.add_plant");
    addBtn.style.marginBottom = "var(--sp-3)";
    addBtn.addEventListener("click", () => {
      if (!canWrite || !onAddPlant) return;
      if (!isCurrentIndoorContext(renderGardenId, renderPlotId)) return;
      onAddPlant(container);
    });
    container.appendChild(addBtn);
  }

  // Header: search + sort (created once, not re-rendered on filter)
  const header = document.createElement("div");
  header.className = "indoor-header";

  const searchInput = document.createElement("input");
  searchInput.type = "search";
  searchInput.placeholder = t("indoor.search_placeholder");
  searchInput.dataset["i18nPlaceholder"] = "indoor.search_placeholder";
  searchInput.setAttribute("aria-label", t("indoor.search_placeholder"));
  searchInput.dataset["i18nAriaLabel"] = "indoor.search_placeholder";
  searchInput.className = "indoor-search";
  searchInput.value = searchQuery;

  const sortSelect = document.createElement("select");
  sortSelect.className = "indoor-sort";
  for (const opt of [
    { value: "name", key: "indoor.sort_name" },
    { value: "room", key: "indoor.sort_room" },
    { value: "category", key: "indoor.sort_category" },
    { value: "quantity", key: "indoor.sort_quantity" },
  ] as const) {
    const option = document.createElement("option");
    option.value = opt.value;
    option.textContent = t(opt.key);
    option.selected = sortBy === opt.value;
    sortSelect.appendChild(option);
  }

  header.append(searchInput, sortSelect);
  container.appendChild(header);

  // Results area (re-rendered on search/sort changes)
  const resultsDiv = document.createElement("div");
  container.appendChild(resultsDiv);

  searchInput.addEventListener("input", () => {
    if (!isCurrentIndoorContext(renderGardenId, renderPlotId)) return;
    searchQuery = searchInput.value;
    renderResultsArea(resultsDiv, container, canWrite);
  });
  sortSelect.addEventListener("change", () => {
    if (!isCurrentIndoorContext(renderGardenId, renderPlotId)) return;
    sortBy = sortSelect.value as typeof sortBy;
    renderResultsArea(resultsDiv, container, canWrite);
  });

  renderResultsArea(resultsDiv, container, canWrite);
}

function renderResultsArea(
  resultsDiv: HTMLElement,
  parentContainer: HTMLElement,
  canWrite: boolean,
): void {
  const renderGardenId = indoorGardenId;
  const renderPlotId = indoorPlotId;
  resultsDiv.textContent = "";
  if (!isCurrentIndoorContext(renderGardenId, renderPlotId)) return;

  const filtered = filteredAndSorted();
  if (filtered.length === 0) {
    const empty = document.createElement("p");
    empty.className = "indoor-empty";
    empty.textContent = indoorPlants.length === 0
      ? t("indoor.no_plants")
      : t("indoor.no_results");
    resultsDiv.appendChild(empty);
    return;
  }

  const groups = groupByRoom(filtered);
  for (const [roomName, plants] of groups) {
    const section = document.createElement("details");
    section.className = "indoor-room-group";
    section.open = true;

    const summary = document.createElement("summary");
    summary.textContent = `${roomName} (${plants.length})`;
    section.appendChild(summary);

    const list = document.createElement("div");
    list.className = "indoor-card-list";

    for (const plant of plants) {
      const wrapper = document.createElement("div");
      wrapper.className = "indoor-card-wrapper";

      // Reuse the standard plant card
      const card = renderPlantCard(plant, indoorPlotId, { canWrite });
      wrapper.appendChild(card);

      // Room label row below the card
      const roomRow = document.createElement("div");
      roomRow.className = "indoor-room-row";
      const roomLabel = document.createElement("span");
      roomLabel.className = "indoor-room-label";
      roomLabel.textContent = plant.room_label
        ? `${t("indoor.room_label")}: ${plant.room_label}`
        : t("indoor.room_label");
      roomRow.appendChild(roomLabel);
      if (canWrite) {
        const roomEditBtn = document.createElement("button");
        roomEditBtn.type = "button";
        roomEditBtn.className = "btn-sm btn-outline";
        roomEditBtn.textContent = plant.room_label
          ? "\u270E"
          : `+ ${t("indoor.room_label")}`;
        roomEditBtn.addEventListener("click", () => {
          if (!canWrite || !isCurrentIndoorContext(renderGardenId, renderPlotId)) return;
          showRoomLabelEditor(
            wrapper,
            plant,
            resultsDiv,
            parentContainer,
            canWrite,
          );
        });
        roomRow.appendChild(roomEditBtn);
      }
      wrapper.appendChild(roomRow);

      list.appendChild(wrapper);
    }

    // Wire edit/remove buttons via event delegation
    list.addEventListener("click", (e) => {
      const target = e.target as HTMLElement;
      const removeBtn = target.closest<HTMLButtonElement>(
        "button[data-remove]",
      );
      if (removeBtn) {
        if (!canWrite) return;
        if (!isCurrentIndoorContext(renderGardenId, renderPlotId)) return;
        const pltId = removeBtn.dataset["remove"];
        if (pltId) {
          void (async () => {
            if (!canWrite) return;
            if (!isCurrentIndoorContext(renderGardenId, renderPlotId)) return;
            try {
              await removePlantFromPlotApi(renderPlotId, pltId);
              if (!isCurrentIndoorContext(renderGardenId, renderPlotId)) return;
              const loaded = await loadIndoorPlants();
              if (!loaded || !isCurrentIndoorContext(renderGardenId, renderPlotId)) return;
              renderResultsArea(resultsDiv, parentContainer, canWrite);
            } catch (err) {
              showToast(getApiErrorMessage(err), "error");
            }
          })();
        }
        return;
      }
      const editBtn = target.closest<HTMLButtonElement>(
        "button[data-edit]",
      );
      if (editBtn) {
        if (!canWrite) return;
        if (!isCurrentIndoorContext(renderGardenId, renderPlotId)) return;
        const pltId = editBtn.dataset["edit"];
        const plant = indoorPlants.find((p) => p.plt_id === pltId);
        if (plant && onEditPlant) onEditPlant(plant);
      }
    });

    section.appendChild(list);
    resultsDiv.appendChild(section);
  }
}

function showRoomLabelEditor(
  wrapper: HTMLElement,
  plant: IndoorPlant,
  resultsDiv: HTMLElement,
  parentContainer: HTMLElement,
  canWrite: boolean,
): void {
  if (!canWrite) return;
  const requestGardenId = indoorGardenId;
  const requestPlotId = indoorPlotId;
  if (!isCurrentIndoorContext(requestGardenId, requestPlotId)) return;

  // Replace room row with an inline edit form
  const existing = wrapper.querySelector(".indoor-room-row");
  if (!existing) return;

  const form = document.createElement("div");
  form.className = "indoor-room-edit";
  const input = document.createElement("input");
  input.type = "text";
  input.maxLength = 50;
  input.value = plant.room_label ?? "";
  input.placeholder = t("indoor.room_placeholder");
  input.className = "indoor-room-input";
  input.setAttribute("list", "indoor-room-datalist-edit");

  const datalist = document.createElement("datalist");
  datalist.id = "indoor-room-datalist-edit";
  for (const label of roomLabels) {
    const opt = document.createElement("option");
    opt.value = label;
    datalist.appendChild(opt);
  }

  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "btn-sm btn-primary";
  saveBtn.textContent = t("common.ok");
  saveBtn.addEventListener("click", async () => {
    if (!canWrite) return;
    if (!isCurrentIndoorContext(requestGardenId, requestPlotId)) return;
    const newRoom = input.value.trim() || null;
    try {
      await updatePlotPlant(
        requestPlotId,
        plant.plt_id,
        plant.quantity,
        newRoom,
      );
      if (!isCurrentIndoorContext(requestGardenId, requestPlotId)) return;
      const loaded = await loadIndoorPlants();
      if (!loaded || !isCurrentIndoorContext(requestGardenId, requestPlotId)) return;
      renderResultsArea(resultsDiv, parentContainer, canWrite);
    } catch (err) {
      showToast(getApiErrorMessage(err), "error");
    }
  });

  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "btn-sm";
  cancelBtn.textContent = "\u00d7";
  cancelBtn.addEventListener("click", () => {
    form.replaceWith(existing);
  });

  form.append(input, datalist, saveBtn, cancelBtn);
  existing.replaceWith(form);
  input.focus();
}
