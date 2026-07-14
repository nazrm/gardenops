import type { Plant } from "../core/models";
import { t } from "../core/i18n";
import type { MediaAsset } from "../services/api";
import { renderPlantCard } from "./plantCard";
import type { PlantAlertType } from "./plantCard";
import { trapFocus } from "./dialogCore";

export interface DrawerParams {
  plotId: string;
  plants: Plant[];
  mediaPreviewByPlantId?: Map<string, MediaAsset | null>;
  plantAlertsByPlantId?: Map<string, PlantAlertType[]>;
  canWrite?: boolean;
  onClose: () => void;
  onSearch: (event: Event) => void;
  onRemove: (pltId: string) => void;
  onEdit: (plant: Plant) => void;
  onDeletePlot?: (() => void) | undefined;
  onCreatePlant?: ((plotId: string) => void) | undefined;
  onCreateCalendarEvent?:
    | ((prefill: { plant_ids?: string[]; plot_ids?: string[] }) => void)
    | undefined;
}

type DrawerPlantSectionParams = Pick<
  DrawerParams,
  | "plotId"
  | "plants"
  | "mediaPreviewByPlantId"
  | "plantAlertsByPlantId"
  | "canWrite"
  | "onClose"
  | "onRemove"
  | "onEdit"
  | "onCreateCalendarEvent"
>;

let activeDrawer: HTMLElement | null = null;
let cleanupFns: Array<() => void> = [];
let activeReturnFocus: HTMLElement | null = null;
let activeReturnPlotId: string | null = null;
let collapsibleId = 0;

function restoreDrawerFocus(
  returnFocus: HTMLElement | null,
  returnPlotId: string | null,
): void {
  const focusTarget = () => {
    const target = returnFocus?.isConnected
      ? returnFocus
      : returnPlotId
        ? document.querySelector<HTMLElement>(
          `.plot[data-plot-id="${CSS.escape(returnPlotId)}"]`,
        )
        : null;
    target?.focus();
  };
  focusTarget();
  window.requestAnimationFrame(focusTarget);
}

export function setCollapsibleSectionState(section: HTMLElement, collapsed: boolean): void {
  section.classList.toggle("collapsed", collapsed);
  const button = section.querySelector<HTMLButtonElement>(".drawer-section-header");
  const body = section.querySelector<HTMLElement>(".drawer-section-body");
  button?.setAttribute("aria-expanded", collapsed ? "false" : "true");
  if (body) body.hidden = collapsed;
}

export function createCollapsibleSection(
  title: string,
  count: number,
  body: HTMLElement,
): HTMLElement {
  const section = document.createElement("div");
  section.className = "drawer-section";

  const header = document.createElement("button");
  header.type = "button";
  header.className = "drawer-section-header";

  const titleEl = document.createElement("span");
  titleEl.className = "drawer-section-title";
  titleEl.textContent = title;

  const countEl = document.createElement("span");
  countEl.className = "drawer-section-count";
  countEl.textContent = String(count);

  const chevron = document.createElement("span");
  chevron.className = "drawer-section-chevron";
  chevron.textContent = "\u25BC";

  const bodyId = `plot-panel-section-${++collapsibleId}`;
  body.id = bodyId;
  header.setAttribute("aria-controls", bodyId);
  header.setAttribute("aria-expanded", "true");
  header.append(titleEl, countEl, chevron);
  header.addEventListener("click", () => {
    setCollapsibleSectionState(section, !section.classList.contains("collapsed"));
  });

  section.append(header, body);
  return section;
}

function buildDrawerPlantsSection(
  params: DrawerPlantSectionParams,
): HTMLElement {
  const plantList = document.createElement("div");
  plantList.className = "plant-list";
  if (params.plants.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-message";
    empty.textContent = t("plots.no_plants_in_plot");
    plantList.appendChild(empty);
  } else {
    plantList.append(
      ...params.plants.map((plant) =>
        renderPlantCard(plant, params.plotId, {
          mediaPreview:
            params.mediaPreviewByPlantId?.get(plant.plt_id) ?? null,
          alertTypes: params.plantAlertsByPlantId?.get(plant.plt_id),
          canWrite: params.canWrite,
          onCreateCalendarEvent: params.onCreateCalendarEvent
            ? (selectedPlant) =>
                params.onCreateCalendarEvent?.({
                  plant_ids: [selectedPlant.plt_id],
                })
            : undefined,
        }),
      ),
    );
  }

  const plantsBody = document.createElement("div");
  plantsBody.className = "drawer-section-body";
  plantsBody.appendChild(plantList);
  const plantsSection = createCollapsibleSection(
    t("plot_drawer.plants_section"),
    params.plants.length,
    plantsBody,
  );
  plantsSection.dataset["drawerPlantsSection"] = "true";

  plantsSection.querySelectorAll<HTMLButtonElement>(
    "button[data-remove]",
  ).forEach((btn) => {
    btn.addEventListener("click", () => {
      const pltId = btn.dataset["remove"];
      if (pltId) params.onRemove(pltId);
    });
  });

  plantsSection.querySelectorAll<HTMLButtonElement>(
    "button[data-edit]",
  ).forEach((btn) => {
    btn.addEventListener("click", () => {
      const pltId = btn.dataset["edit"];
      const plant = params.plants.find((candidate) => candidate.plt_id === pltId);
      if (plant) params.onEdit(plant);
    });
  });
  plantsSection.querySelectorAll<HTMLButtonElement>(
    "button[data-calendar-create-plant]",
  ).forEach((btn) => {
    btn.addEventListener("click", () => {
      const pltId = btn.dataset["calendarCreatePlant"];
      if (!pltId || !params.onCreateCalendarEvent) return;
      dismissDrawer();
      params.onClose();
      params.onCreateCalendarEvent({ plant_ids: [pltId] });
    });
  });

  wireCardDrag(plantsSection);
  return plantsSection;
}

export function showDrawer(params: DrawerParams): void {
  const focusedBeforeOpen = document.activeElement instanceof HTMLElement
    ? document.activeElement
    : null;
  dismissDrawer();

  const { plotId, plants, mediaPreviewByPlantId, plantAlertsByPlantId, onClose, onSearch, onRemove, onEdit } = params;

  const backdrop = document.createElement("div");
  backdrop.className = "drawer-backdrop";
  backdrop.setAttribute("aria-hidden", "true");

  const drawer = document.createElement("aside");
  drawer.className = "drawer";
  drawer.setAttribute("role", "dialog");
  drawer.setAttribute("aria-modal", "true");
  drawer.setAttribute("aria-labelledby", "plot-drawer-title");
  drawer.tabIndex = -1;

  const header = document.createElement("div");
  header.className = "drawer-header";

  const title = document.createElement("h2");
  title.id = "plot-drawer-title";
  title.textContent = plotId;

  const closeBtn = document.createElement("button");
  closeBtn.className = "close-btn";
  closeBtn.setAttribute("aria-label", t("media.close_viewer"));
  closeBtn.type = "button";
  closeBtn.textContent = "\u00d7";

  const headerActions = document.createElement("div");
  headerActions.className = "drawer-header-actions";

  if (params.canWrite !== false && params.onDeletePlot) {
    const deletePlotBtn = document.createElement("button");
    deletePlotBtn.type = "button";
    deletePlotBtn.className = "drawer-delete-plot-btn";
    deletePlotBtn.textContent = t("popover.delete_plot");
    deletePlotBtn.addEventListener("click", () => {
      params.onDeletePlot?.();
    });
    headerActions.appendChild(deletePlotBtn);
  }

  headerActions.appendChild(closeBtn);
  header.append(title, headerActions);

  const addPlantSection = document.createElement("div");
  addPlantSection.className = "add-plant-section";
  let searchInput: HTMLInputElement | null = null;

  if (params.canWrite !== false) {
    searchInput = document.createElement("input");
    searchInput.type = "text";
    searchInput.id = "drawer-plant-search";
    searchInput.className = "plant-search-input";
    searchInput.placeholder = t("plants.search_placeholder");
    searchInput.setAttribute("aria-label", t("plants.search_placeholder"));

    const searchResults = document.createElement("div");
    searchResults.id = "drawer-search-results";
    searchResults.className = "search-results";

    addPlantSection.append(searchInput, searchResults);

    if (params.onCreatePlant) {
      const createLink = document.createElement("button");
      createLink.type = "button";
      createLink.className = "btn-link create-plant-link";
      createLink.textContent = t("plants.search_create_manual");
      createLink.addEventListener("click", () => {
        params.onCreatePlant!(plotId);
      });
      addPlantSection.appendChild(createLink);
    }
    if (params.onCreateCalendarEvent) {
      const calendarLink = document.createElement("button");
      calendarLink.type = "button";
      calendarLink.className = "btn-link create-plant-link";
      calendarLink.dataset["createCalendarEventPlot"] = plotId;
      calendarLink.textContent = t("calendar.new_event");
      calendarLink.addEventListener("click", () => {
        dismissDrawer();
        onClose();
        params.onCreateCalendarEvent?.({ plot_ids: [plotId] });
      });
      addPlantSection.appendChild(calendarLink);
    }
  }

  const journalPreview = document.createElement("div");
  journalPreview.className = "drawer-journal-preview";
  const mediaPreview = document.createElement("div");
  mediaPreview.className = "drawer-media-preview";

  const plantsSection = buildDrawerPlantsSection({
    plotId,
    plants,
    ...(mediaPreviewByPlantId
      ? { mediaPreviewByPlantId }
      : {}),
    ...(plantAlertsByPlantId
      ? { plantAlertsByPlantId }
      : {}),
    ...(params.canWrite !== undefined
      ? { canWrite: params.canWrite }
      : {}),
    onClose,
    onRemove,
    onEdit,
    ...(params.onCreateCalendarEvent
      ? { onCreateCalendarEvent: params.onCreateCalendarEvent }
      : {}),
  });

  const tasksPreview = document.createElement("div");
  tasksPreview.className = "drawer-tasks-preview";

  drawer.append(
    header,
    tasksPreview,
    plantsSection,
    journalPreview,
    mediaPreview,
  );
  if (params.canWrite !== false) {
    drawer.insertBefore(addPlantSection, tasksPreview);
  }

  document.body.appendChild(backdrop);
  document.body.appendChild(drawer);
  activeDrawer = drawer;
  activeReturnPlotId = plotId;
  activeReturnFocus = focusedBeforeOpen?.isConnected
    ? focusedBeforeOpen
    : document.querySelector<HTMLElement>(`.plot[data-plot-id="${CSS.escape(plotId)}"]`);

  requestAnimationFrame(() => {
    backdrop.classList.add("drawer-backdrop-visible");
    drawer.classList.add("drawer-open");
  });

  const close = () => {
    const returnFocus = activeReturnFocus;
    const returnPlotId = activeReturnPlotId;
    dismissDrawer();
    onClose();
    restoreDrawerFocus(returnFocus, returnPlotId);
  };

  closeBtn?.addEventListener("click", close);
  backdrop.addEventListener("click", close);

  const onEscape = (e: KeyboardEvent) => {
    if (e.key !== "Escape") return;
    const childDialog = Array.from(document.querySelectorAll<HTMLElement>(".modal[aria-modal='true']"))
      .find((candidate) => candidate.isConnected && !drawer.contains(candidate));
    if (childDialog) return;
    e.preventDefault();
    close();
  };
  window.addEventListener("keydown", onEscape);
  const releaseFocusTrap = trapFocus(drawer);

  searchInput?.addEventListener("input", onSearch);

  cleanupFns.push(() => {
    window.removeEventListener("keydown", onEscape);
    releaseFocusTrap();
  });

  (searchInput ?? closeBtn).focus();
}

export function dismissDrawer(restoreFocus = false): void {
  const returnFocus = activeReturnFocus;
  const returnPlotId = activeReturnPlotId;
  for (const fn of cleanupFns) fn();
  cleanupFns = [];

  if (activeDrawer) {
    activeDrawer.remove();
    activeDrawer = null;
  }
  document.querySelector(".drawer-backdrop")?.remove();
  activeReturnFocus = null;
  activeReturnPlotId = null;
  if (restoreFocus) {
    restoreDrawerFocus(returnFocus, returnPlotId);
  }
}

export function isDrawerOpen(): boolean {
  return activeDrawer !== null;
}

export function updateDrawerPlantsSection(
  params: DrawerPlantSectionParams,
): void {
  if (!activeDrawer) return;
  const nextSection = buildDrawerPlantsSection(params);
  const currentSection = activeDrawer.querySelector<HTMLElement>(
    "[data-drawer-plants-section]",
  );
  if (currentSection?.classList.contains("collapsed")) {
    setCollapsibleSectionState(nextSection, true);
  }
  if (currentSection) {
    currentSection.replaceWith(nextSection);
    return;
  }
  const journalPreview = activeDrawer.querySelector(".drawer-journal-preview");
  if (journalPreview) {
    activeDrawer.insertBefore(nextSection, journalPreview);
  } else {
    activeDrawer.appendChild(nextSection);
  }
}

function wireCardDrag(container: HTMLElement): void {
  container.querySelectorAll<HTMLElement>(
    ".plant-card[draggable]",
  ).forEach((card) => {
    card.addEventListener("dragstart", (e) => {
      const pltId = card.dataset["pltId"] ?? "";
      const fromPlot = card.dataset["fromPlot"] ?? "";
      if (e.dataTransfer) {
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("application/plant-id", pltId);
        e.dataTransfer.setData("application/from-plot", fromPlot);
      }
      card.classList.add("dragging");
      const backdrop = document.querySelector<HTMLElement>(
        ".drawer-backdrop",
      );
      if (backdrop) backdrop.style.pointerEvents = "none";
    });
    card.addEventListener("dragend", () => {
      card.classList.remove("dragging");
      const backdrop = document.querySelector<HTMLElement>(
        ".drawer-backdrop",
      );
      if (backdrop) backdrop.style.pointerEvents = "";
    });
  });
}

export function getDrawerSearchResults(): HTMLElement | null {
  return activeDrawer?.querySelector("#drawer-search-results") ?? null;
}

export function getDrawerJournalPreview(): HTMLElement | null {
  return activeDrawer?.querySelector(".drawer-journal-preview") ?? null;
}

export function getDrawerMediaPreview(): HTMLElement | null {
  return activeDrawer?.querySelector(".drawer-media-preview") ?? null;
}

export function getDrawerTasksPreview(): HTMLElement | null {
  return activeDrawer?.querySelector(".drawer-tasks-preview") ?? null;
}
