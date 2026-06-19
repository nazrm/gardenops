import type { Plant } from "../core/models";
import { formatPlantCategoryLabel, t } from "../core/i18n";
import { renderEmptyState } from "./emptyState";
import {
  clearVirtualTableBody,
  renderVirtualTableBody,
} from "./virtualTable";
import {
  clearVirtualList,
  renderVirtualList,
} from "./virtualList";

export type CareSortField = "name" | "latin";
export type CareSortDir = "asc" | "desc";

const CATEGORY_EMOJI: Record<string, string> = {
  "løk": "\uD83E\uDDC5",
  "frø": "\uD83C\uDF31",
  "busker": "\uD83C\uDF3F",
  "baerbusker": "\uD83C\uDF53",
  "trær": "\uD83C\uDF33",
};

export function filterCarePlants(
  plants: Plant[],
  query: string,
  category: string,
): Plant[] {
  const q = query.trim().toLowerCase();
  return plants.filter((p) => {
    const matchCat = !category || p.category === category;
    if (!q) return matchCat;
    const haystack =
      `${p.name} ${p.latin || ""} ${p.category}`.toLowerCase();
    return matchCat && haystack.includes(q);
  });
}

export function sortCarePlants(
  plants: Plant[],
  field: CareSortField,
  dir: CareSortDir,
): Plant[] {
  const sorted = [...plants];
  const mul = dir === "asc" ? 1 : -1;
  sorted.sort((a, b) => {
    const sa = (a[field] ?? "").toLowerCase();
    const sb = (b[field] ?? "").toLowerCase();
    return sa.localeCompare(sb) * mul;
  });
  return sorted;
}

export function renderCareTableHead(thead: HTMLElement): void {
  const row = document.createElement("tr");

  const name = document.createElement("th");
  name.className = "sortable col-name";
  name.dataset["sort"] = "name";
  name.textContent = t("care.field_name");

  const latin = document.createElement("th");
  latin.className = "sortable col-latin";
  latin.dataset["sort"] = "latin";
  latin.textContent = t("care.field_latin");

  row.append(name, latin);
  thead.replaceChildren(row);
}

export interface CareTableCallbacks {
  onPlantClick: (plant: Plant) => void;
}

function previewText(value: string): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (normalized.length <= 120) return normalized;
  return `${normalized.slice(0, 117).trimEnd()}...`;
}

export function renderCareTableBody(
  tbody: HTMLElement,
  plants: Plant[],
  callbacks: CareTableCallbacks,
): void {
  if (plants.length === 0) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 2;
    cell.className = "empty-table";
    cell.textContent = t("plants.no_matches");
    row.appendChild(cell);
    renderVirtualTableBody({
      tbody,
      items: [],
      totalColumns: 2,
      estimateRowHeight: 48,
      createRow: () => row,
      emptyRow: () => row,
    });
    return;
  }

  const createRow = (plant: Plant): HTMLTableRowElement => {
    const row = document.createElement("tr");
    row.className = "care-row";
    row.dataset["pltId"] = plant.plt_id;

    const nameCell = document.createElement("td");
    nameCell.className = "col-name";
    const emoji = CATEGORY_EMOJI[plant.category] ?? "";
    if (emoji) {
      const prefix = document.createElement("span");
      prefix.className = "cat-emoji";
      prefix.title = formatPlantCategoryLabel(plant.category);
      prefix.textContent = emoji;
      nameCell.append(prefix, document.createTextNode(" "));
    }
    nameCell.append(document.createTextNode(plant.name));

    const latinCell = document.createElement("td");
    latinCell.className = "col-latin";
    latinCell.textContent = plant.latin || "";

    row.append(nameCell, latinCell);
    row.addEventListener("click", () => callbacks.onPlantClick(plant));
    return row;
  };

  renderVirtualTableBody({
    tbody,
    items: plants,
    totalColumns: 2,
    estimateRowHeight: 48,
    overscan: 8,
    createRow,
    emptyRow: () => {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 2;
      cell.className = "empty-table";
      cell.textContent = t("plants.no_matches");
      row.appendChild(cell);
      return row;
    },
  });
}

export function clearCareTableBody(tbody: HTMLElement): void {
  clearVirtualTableBody(tbody);
}

export function renderCareMobileCards(
  container: HTMLElement,
  plants: Plant[],
  callbacks: CareTableCallbacks,
): void {
  const renderEmpty = (): void => {
    renderEmptyState(container, {
      icon: "\uD83C\uDF3F",
      headline: t("plants.no_matches"),
    });
  };

  const createCard = (plant: Plant): HTMLElement => {
    const button = document.createElement("button");
    button.className = "care-mobile-card";
    button.type = "button";
    button.dataset["carePlt"] = plant.plt_id;
    button.setAttribute("aria-label", `Open care instructions for ${plant.name}`);

    const header = document.createElement("div");
    header.className = "mobile-data-card-header";

    const copy = document.createElement("div");
    copy.className = "mobile-data-copy";

    const title = document.createElement("h3");
    title.className = "mobile-data-title";
    title.textContent = plant.name;

    const subtitle = document.createElement("p");
    subtitle.className = "mobile-data-subtitle";
    subtitle.textContent = plant.latin || t("plants.no_latin_name");

    copy.append(title, subtitle);
    header.appendChild(copy);

    const emoji = CATEGORY_EMOJI[plant.category] ?? "";
    const catLabel = plant.category ? `${emoji ? `${emoji} ` : ""}${formatPlantCategoryLabel(plant.category)}` : "";
    if (catLabel) {
      const chip = document.createElement("span");
      chip.className = "mobile-data-chip";
      chip.textContent = catLabel;
      header.appendChild(chip);
    }

    const previewList = document.createElement("div");
    previewList.className = "care-mobile-preview-list";
    const previews = CARE_FIELDS
      .filter((field) => {
        const value = plant[field.key];
        return typeof value === "string" && value.trim().length > 0;
      })
      .slice(0, 2);

    if (previews.length === 0) {
      const empty = document.createElement("p");
      empty.className = "care-mobile-empty";
      empty.textContent = t("care.no_notes");
      previewList.appendChild(empty);
    } else {
      previews.forEach((field) => {
        const preview = document.createElement("div");
        preview.className = "care-mobile-preview";

        const label = document.createElement("span");
        label.className = "mobile-data-label";
        label.textContent = field.label;

        const value = document.createElement("p");
        value.textContent = previewText((plant[field.key] as string) || "");

        preview.append(label, value);
        previewList.appendChild(preview);
      });
    }

    const cta = document.createElement("span");
    cta.className = "care-mobile-cta";
    cta.textContent = t("care.open_details");

    button.append(header, previewList, cta);
    button.addEventListener("click", () => callbacks.onPlantClick(plant));
    return button;
  };

  renderVirtualList({
    container,
    items: plants,
    estimateItemHeight: 190,
    overscan: 5,
    createItem: createCard,
    renderEmpty,
  });
}

export function clearCareMobileCards(container: HTMLElement): void {
  clearVirtualList(container);
}

interface CareField {
  label: string;
  key: keyof Plant;
}

const CARE_FIELDS: CareField[] = [
  { label: "care.field_watering", key: "care_watering" },
  { label: "care.field_soil", key: "care_soil" },
  { label: "care.field_planting", key: "care_planting" },
  { label: "care.field_maintenance", key: "care_maintenance" },
  { label: "care.field_notes", key: "care_notes" },
];

export function showCareOverlay(plant: Plant): void {
  dismissCareOverlay();

  const overlay = document.createElement("div");
  overlay.className = "modal care-overlay";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-label", t("care.overlay_title", { name: plant.name }));

  const content = document.createElement("div");
  content.className = "modal-content care-overlay-content";

  const header = document.createElement("div");
  header.className = "care-overlay-header";

  const copy = document.createElement("div");
  const title = document.createElement("h3");
  title.textContent = plant.name;
  const latin = document.createElement("p");
  latin.className = "care-overlay-latin";
  latin.textContent = plant.latin || "";
  const category = document.createElement("span");
  category.className = "care-overlay-cat";
  const emoji = CATEGORY_EMOJI[plant.category] ?? "";
  category.textContent = plant.category ? `${emoji ? `${emoji} ` : ""}${formatPlantCategoryLabel(plant.category)}` : "";
  copy.append(title, latin, category);

  const closeBtn = document.createElement("button");
  closeBtn.className = "care-overlay-close";
  closeBtn.setAttribute("aria-label", t("common.close"));
  closeBtn.type = "button";
  closeBtn.textContent = "\u00d7";
  closeBtn.addEventListener("click", dismissCareOverlay);

  header.append(copy, closeBtn);
  content.appendChild(header);

  const sections = CARE_FIELDS.filter((field) => {
    const value = plant[field.key];
    return typeof value === "string" && value.length > 0;
  });

  if (sections.length === 0) {
    const empty = document.createElement("p");
    empty.className = "care-overlay-empty";
    empty.textContent = t("care.overlay_empty");
    content.appendChild(empty);
  } else {
    sections.forEach((field) => {
      const section = document.createElement("div");
      section.className = "care-overlay-section";

      const heading = document.createElement("h4");
      heading.textContent = t(field.label);

      const value = document.createElement("p");
      value.textContent = plant[field.key] as string;

      section.append(heading, value);
      content.appendChild(section);
    });
  }

  overlay.appendChild(content);
  document.body.appendChild(overlay);
  activeOverlay = overlay;

  closeBtn.focus();

  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) dismissCareOverlay();
  });

  const onEscape = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      dismissCareOverlay();
      window.removeEventListener("keydown", onEscape);
    }
  };
  window.addEventListener("keydown", onEscape);
}

let activeOverlay: HTMLElement | null = null;

function dismissCareOverlay(): void {
  const el = activeOverlay ?? document.querySelector(".care-overlay");
  if (el) {
    el.remove();
    activeOverlay = null;
  }
}
