import type { Plant } from "../core/models";
import { t } from "../core/i18n";
import type { MediaAsset } from "../services/api";
import { renderPlantCard } from "./plantCard";
import type { PlantAlertType } from "./plantCard";
import { trapFocus } from "./dialogCore";
import { createCollapsibleSection, setCollapsibleSectionState } from "./drawer";

type SnapState = "peek" | "half" | "full";

const SNAP_HEIGHTS: Record<SnapState, string> = {
  peek: "60px",
  half: "40vh",
  full: "85vh",
};

export interface BottomSheetParams {
  plotId: string;
  plants: Plant[];
  mediaPreviewByPlantId?: Map<string, MediaAsset | null>;
  plantAlertsByPlantId?: Map<string, PlantAlertType[]>;
  canWrite?: boolean;
  onClose: () => void;
  onSearch: (event: Event) => void;
  onRemove: (pltId: string) => void;
  onEdit: (plant: Plant) => void;
  onEditPlot?: (() => void) | undefined;
  onDeletePlot?: (() => void) | undefined;
  onCreatePlant?: ((plotId: string) => void) | undefined;
  onCreateCalendarEvent?:
    | ((prefill: { plant_ids?: string[]; plot_ids?: string[] }) => void)
    | undefined;
}

type BottomSheetPlantSectionParams = Pick<
  BottomSheetParams,
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

let activeSheet: HTMLElement | null = null;
let currentSnap: SnapState = "half";
let dragStartY = 0;
let dragStartHeight = 0;
let cleanupFns: Array<() => void> = [];
let activeReturnFocus: HTMLElement | null = null;
let activeReturnPlotId: string | null = null;

function restoreSheetFocus(
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

function buildBottomSheetPlantsSection(
  params: BottomSheetPlantSectionParams,
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
  plantsSection.dataset["sheetPlantsSection"] = "true";

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
      dismissBottomSheet();
      params.onClose();
      params.onCreateCalendarEvent({ plant_ids: [pltId] });
    });
  });

  wireCardDrag(plantsSection);
  return plantsSection;
}

export function showBottomSheet(params: BottomSheetParams): void {
  const focusedBeforeOpen = document.activeElement instanceof HTMLElement
    ? document.activeElement
    : null;
  dismissBottomSheet();

  const { plotId, plants, mediaPreviewByPlantId, plantAlertsByPlantId, onClose, onSearch, onRemove, onEdit } = params;
  currentSnap = "half";

  const sheet = document.createElement("div");
  sheet.className = "bottom-sheet";
  sheet.setAttribute("role", "dialog");
  sheet.setAttribute("aria-modal", "true");
  sheet.setAttribute("aria-labelledby", "plot-bottom-sheet-title");
  sheet.tabIndex = -1;

  const handleBar = document.createElement("button");
  handleBar.type = "button";
  handleBar.className = "sheet-handle-bar";
  handleBar.setAttribute("aria-label", t("plot_drawer.resize_sheet"));
  const handleIndicator = document.createElement("div");
  handleIndicator.className = "sheet-handle";
  handleIndicator.setAttribute("aria-hidden", "true");
  handleBar.appendChild(handleIndicator);
  applySnapState(sheet, handleBar, currentSnap);

  const header = document.createElement("div");
  header.className = "sheet-header";

  const title = document.createElement("h2");
  title.id = "plot-bottom-sheet-title";
  title.textContent = plotId;

  const closeBtn = document.createElement("button");
  closeBtn.className = "close-btn";
  closeBtn.setAttribute("aria-label", t("media.close_viewer"));
  closeBtn.type = "button";
  closeBtn.textContent = "\u00d7";

  const headerActions = document.createElement("div");
  headerActions.className = "sheet-header-actions";

  if (params.canWrite !== false && params.onEditPlot) {
    const editPlotBtn = document.createElement("button");
    editPlotBtn.type = "button";
    editPlotBtn.className = "drawer-edit-plot-btn";
    editPlotBtn.dataset["editPlot"] = plotId;
    editPlotBtn.textContent = t("common.edit");
    editPlotBtn.setAttribute("aria-label", t("popover.edit_plot"));
    editPlotBtn.title = t("popover.edit_plot");
    editPlotBtn.addEventListener("click", () => {
      dismissBottomSheet(true);
      onClose();
      window.requestAnimationFrame(() => params.onEditPlot?.());
    });
    headerActions.appendChild(editPlotBtn);
  }

  if (params.canWrite !== false && params.onDeletePlot) {
    const deletePlotBtn = document.createElement("button");
    deletePlotBtn.type = "button";
    deletePlotBtn.className = "drawer-delete-plot-btn";
    deletePlotBtn.dataset["deletePlot"] = plotId;
    deletePlotBtn.textContent = t("common.delete");
    deletePlotBtn.setAttribute("aria-label", t("popover.delete_plot"));
    deletePlotBtn.title = t("popover.delete_plot");
    deletePlotBtn.addEventListener("click", () => {
      params.onDeletePlot?.();
    });
    headerActions.appendChild(deletePlotBtn);
  }

  headerActions.appendChild(closeBtn);
  header.append(title, headerActions);

  const body = document.createElement("div");
  body.className = "sheet-body";

  const addPlantSection = document.createElement("div");
  addPlantSection.className = "add-plant-section";
  let searchInput: HTMLInputElement | null = null;

  if (params.canWrite !== false) {
    searchInput = document.createElement("input");
    searchInput.type = "text";
    searchInput.id = "sheet-plant-search";
    searchInput.className = "plant-search-input";
    searchInput.placeholder = t("plants.search_placeholder");
    searchInput.setAttribute("aria-label", t("plants.search_placeholder"));

    const searchResults = document.createElement("div");
    searchResults.id = "sheet-search-results";
    searchResults.className = "search-results";

    addPlantSection.append(searchInput, searchResults);

    if (params.onCreatePlant) {
      const createLink = document.createElement("button");
      createLink.type = "button";
      createLink.className = "btn-link create-plant-link";
      createLink.textContent = t("plants.search_create_manual");
      createLink.addEventListener("click", () => {
        dismissBottomSheet(true);
        onClose();
        window.requestAnimationFrame(() => params.onCreatePlant?.(plotId));
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
        dismissBottomSheet();
        onClose();
        params.onCreateCalendarEvent?.({ plot_ids: [plotId] });
      });
      addPlantSection.appendChild(calendarLink);
    }
  }

  const journalPreview = document.createElement("div");
  journalPreview.className = "sheet-journal-preview";
  const mediaPreview = document.createElement("div");
  mediaPreview.className = "sheet-media-preview";

  const plantsSection = buildBottomSheetPlantsSection({
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
  tasksPreview.className = "sheet-tasks-preview";

  body.append(
    tasksPreview,
    plantsSection,
    journalPreview,
    mediaPreview,
  );
  if (params.canWrite !== false) {
    body.insertBefore(addPlantSection, tasksPreview);
  }
  sheet.append(handleBar, header, body);

  document.body.appendChild(sheet);
  activeSheet = sheet;
  activeReturnPlotId = plotId;
  activeReturnFocus = focusedBeforeOpen?.isConnected
    ? focusedBeforeOpen
    : document.querySelector<HTMLElement>(`.plot[data-plot-id="${CSS.escape(plotId)}"]`);

  requestAnimationFrame(() => sheet.classList.add("sheet-visible"));

  if (window.visualViewport) {
    const onResize = () => {
      const vv = window.visualViewport!;
      const keyboardH = window.innerHeight - vv.height;
      sheet.style.paddingBottom = keyboardH > 50 ? `${keyboardH}px` : "";
    };
    window.visualViewport.addEventListener("resize", onResize);
    cleanupFns.push(() => window.visualViewport?.removeEventListener("resize", onResize));
  }

  const close = () => {
    const returnFocus = activeReturnFocus;
    const returnPlotId = activeReturnPlotId;
    dismissBottomSheet();
    onClose();
    restoreSheetFocus(returnFocus, returnPlotId);
  };
  closeBtn.addEventListener("click", close);

  initSwipe(handleBar, sheet);
  searchInput?.addEventListener("input", onSearch);

  const onEscape = (e: KeyboardEvent) => {
    if (e.key !== "Escape") return;
    const childDialog = Array.from(document.querySelectorAll<HTMLElement>(".modal[aria-modal='true']"))
      .find((candidate) => candidate.isConnected && !sheet.contains(candidate));
    if (childDialog) return;
    e.preventDefault();
    close();
  };
  window.addEventListener("keydown", onEscape);
  const releaseFocusTrap = trapFocus(sheet);
  cleanupFns.push(() => {
    window.removeEventListener("keydown", onEscape);
    releaseFocusTrap();
  });

  (searchInput ?? closeBtn).focus();
}

function initSwipe(handle: HTMLButtonElement, sheet: HTMLElement): void {
  let lastDragY = 0;
  let suppressNextClick = false;

  handle.addEventListener("click", () => {
    if (suppressNextClick) {
      suppressNextClick = false;
      return;
    }
    const states: SnapState[] = ["peek", "half", "full"];
    const nextIndex = (states.indexOf(currentSnap) + 1) % states.length;
    applySnapState(sheet, handle, states[nextIndex]!);
  });

  handle.addEventListener("pointerdown", (e) => {
    dragStartY = e.clientY;
    lastDragY = e.clientY;
    dragStartHeight = sheet.getBoundingClientRect().height;
    handle.setPointerCapture(e.pointerId);
    sheet.style.transition = "none";
    sheet.style.willChange = "transform";

    const onMove = (ev: PointerEvent) => {
      lastDragY = ev.clientY;
      const dy = dragStartY - ev.clientY;
      const clampedH = Math.max(40, dragStartHeight + dy);
      const offset = dragStartHeight - clampedH;
      sheet.style.transform = `translateY(${offset}px)`;
    };

    const onUp = () => {
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      const dy = dragStartY - lastDragY;
      suppressNextClick = Math.abs(dy) > 8;
      if (suppressNextClick) {
        window.setTimeout(() => {
          suppressNextClick = false;
        }, 0);
      }
      const finalHeight = Math.max(40, dragStartHeight + dy);
      sheet.style.transform = "";
      sheet.style.willChange = "";
      sheet.style.height = `${finalHeight}px`;
      sheet.style.transition = "";
      snapToNearest(sheet, handle);
    };

    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
  });
}

function applySnapState(
  sheet: HTMLElement,
  handle: HTMLButtonElement,
  state: SnapState,
): void {
  currentSnap = state;
  sheet.style.height = SNAP_HEIGHTS[currentSnap];
  sheet.dataset["snapState"] = currentSnap;
  handle.setAttribute("aria-expanded", currentSnap === "peek" ? "false" : "true");
}

function snapToNearest(sheet: HTMLElement, handle: HTMLButtonElement): void {
  const h = sheet.getBoundingClientRect().height;
  const vh = window.innerHeight;

  const targets = [
    { state: "peek" as SnapState, px: 60 },
    { state: "half" as SnapState, px: vh * 0.4 },
    { state: "full" as SnapState, px: vh * 0.85 },
  ];

  let closest = targets[0]!;
  let minDist = Math.abs(h - closest.px);
  for (const t of targets) {
    const dist = Math.abs(h - t.px);
    if (dist < minDist) {
      closest = t;
      minDist = dist;
    }
  }

  applySnapState(sheet, handle, closest.state);
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
    });
    card.addEventListener("dragend", () => {
      card.classList.remove("dragging");
    });
  });
}

export function dismissBottomSheet(restoreFocus = false): void {
  const returnFocus = activeReturnFocus;
  const returnPlotId = activeReturnPlotId;
  for (const fn of cleanupFns) fn();
  cleanupFns = [];
  if (activeSheet) {
    activeSheet.remove();
    activeSheet = null;
  }
  activeReturnFocus = null;
  activeReturnPlotId = null;
  if (restoreFocus) {
    restoreSheetFocus(returnFocus, returnPlotId);
  }
}

export function isBottomSheetOpen(): boolean {
  return activeSheet !== null;
}

export function updateBottomSheetPlantsSection(
  params: BottomSheetPlantSectionParams,
): void {
  if (!activeSheet) return;
  const nextSection = buildBottomSheetPlantsSection(params);
  const currentSection = activeSheet.querySelector<HTMLElement>(
    "[data-sheet-plants-section]",
  );
  if (currentSection?.classList.contains("collapsed")) {
    setCollapsibleSectionState(nextSection, true);
  }
  if (currentSection) {
    currentSection.replaceWith(nextSection);
    return;
  }
  const journalPreview = activeSheet.querySelector(".sheet-journal-preview");
  if (journalPreview) {
    journalPreview.parentElement?.insertBefore(nextSection, journalPreview);
  } else {
    activeSheet.appendChild(nextSection);
  }
}

export function getSheetSearchResults(): HTMLElement | null {
  return activeSheet?.querySelector("#sheet-search-results") ?? null;
}

export function getSheetJournalPreview(): HTMLElement | null {
  return activeSheet?.querySelector(".sheet-journal-preview") ?? null;
}

export function getSheetMediaPreview(): HTMLElement | null {
  return activeSheet?.querySelector(".sheet-media-preview") ?? null;
}

export function getSheetTasksPreview(): HTMLElement | null {
  return activeSheet?.querySelector(".sheet-tasks-preview") ?? null;
}
