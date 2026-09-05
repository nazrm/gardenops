import type { Plant, Plot } from "../core/models";
import { t } from "../core/i18n";
import { sanitizeUrl } from "../core/sanitize";
import { fetchJournalEntriesApi, getActiveGardenContext, getApiErrorMessage, getPlantPlots } from "../services/api";
import { createModal } from "./dialogCore";
import { renderPlotJournalPreviewLazy } from "./journalPreviewLoader";

export interface PlantSummaryOptions {
  plant: Plant;
  plots: Plot[];
  canWrite: boolean;
  getLocationLabel?: (plotId: string) => string;
  onLocation: (plotId: string) => void;
  onHistory: (onReturn: () => void) => void;
  onEdit: () => void;
  onPlace: () => void;
  onRecord: () => void;
  onReportIssue: () => void;
  onClose?: () => void;
}

let dismissSummary: (() => void) | null = null;

export function closePlantSummary(): void {
  dismissSummary?.();
  dismissSummary = null;
}

export function showPlantSummary(options: PlantSummaryOptions): void {
  closePlantSummary();
  const { plant } = options;
  const gardenId = getActiveGardenContext();
  const { dialog, close } = createModal(plant.name, '<div class="modal-content plant-summary"></div>', {
    onClose: () => options.onClose?.(),
  });
  dismissSummary = close;
  const content = dialog.querySelector<HTMLElement>(".modal-content")!;
  const heading = document.createElement("h3");
  heading.textContent = plant.name;
  const identity = document.createElement("p");
  identity.textContent = [plant.latin, plant.plt_id].filter(Boolean).join(" · ");
  const locations = document.createElement("section");
  const locationTitle = document.createElement("h4");
  locationTitle.textContent = t("experience.current_locations");
  const locationList = document.createElement("div");
  locationList.className = "button-row";
  locations.append(locationTitle, locationList);
  const status = document.createElement("p");
  status.textContent = plant.seen_growing_date
    ? `${t(plant.seen_growing === false ? "experience.not_seen_on" : "experience.seen_on", { date: plant.seen_growing_date })}`
    : t("experience.no_observation");
  if (plant.last_bloomed_on) status.append(` · ${t("experience.bloomed_on", { date: plant.last_bloomed_on })}`);
  const actions = document.createElement("div");
  actions.className = "button-row";
  const button = (label: string, action: () => void, parent = actions) => {
    const el = document.createElement("button");
    el.type = "button";
    el.textContent = label;
    el.addEventListener("click", action);
    parent.append(el);
    return el;
  };
  const history = () => {
    close();
    options.onHistory(() => showPlantSummary(options));
  };
  button(t("experience.history"), history);
  const safeLink = sanitizeUrl(plant.link ?? "");
  if (safeLink) {
    const reference = document.createElement("a");
    reference.href = safeLink;
    reference.target = "_blank";
    reference.rel = "noopener noreferrer";
    reference.textContent = t("experience.reference");
    actions.append(reference);
  }
  if (options.canWrite) {
    button(t("experience.record_observation"), options.onRecord);
    button(t("experience.report_issue"), options.onReportIssue);
    button(t("plants.edit"), () => { close(); options.onEdit(); });
  }
  const preview = document.createElement("div");
  preview.className = "plant-journal-preview-container";
  content.append(heading, identity, locations, status, actions, preview);
  const current = () => dialog.isConnected && gardenId === getActiveGardenContext();
  const loadLocations = async () => {
    locationList.textContent = t("common.loading");
    try {
      const ids = await getPlantPlots(plant.plt_id, { gardenId });
      if (!current()) return;
      locationList.replaceChildren();
      if (!ids.length) {
        const empty = document.createElement("p");
        empty.textContent = t("experience.no_current_location");
        locationList.append(empty);
        if (options.canWrite && plant.can_assign) {
          button(t("map.place_plant"), () => { close(); options.onPlace(); }, locationList);
        }
      }
      for (const id of ids) {
        const plot = options.plots.find((item) => item.plot_id === id);
        button(options.getLocationLabel?.(id) || plot?.display_name || id, () => { close(); options.onLocation(id); }, locationList);
      }
    } catch (error) {
      if (!current()) return;
      locationList.textContent = getApiErrorMessage(error);
      button(t("common.retry"), () => void loadLocations(), locationList);
    }
  };
  const loadHistory = async () => {
    preview.textContent = t("common.loading");
    try {
      const result = await fetchJournalEntriesApi({ plant_id: plant.plt_id, limit: 3, offset: 0 }, { gardenId });
      if (!current()) return;
      renderPlotJournalPreviewLazy(preview, result.entries, history);
    } catch (error) {
      if (!current()) return;
      preview.textContent = getApiErrorMessage(error);
      button(t("common.retry"), () => void loadHistory(), preview);
    }
  };
  void loadLocations();
  void loadHistory();
  actions.querySelector("button")?.focus();
}
