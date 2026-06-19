import type { Plot } from "../core/models";
import { t } from "../core/i18n";

const ZOOM_MIN = 1.0;
const ZOOM_MAX = 3.0;
const ZOOM_STEP = 0.5;
const SVG_NS = "http://www.w3.org/2000/svg";

let zoomLevel = 1.0;
const zoomControlsByGrid = new WeakMap<HTMLElement, HTMLElement>();
const gestureBindings = new WeakMap<HTMLElement, {
  target: HTMLElement;
  cleanup: () => void;
}>();
const gridDelegationState = new WeakMap<HTMLElement, {
  byCell: Map<string, Plot>;
  callbacks: GridCallbacks;
}>();

function clampZoom(z: number): number {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));
}

function applyZoom(grid: HTMLElement): void {
  grid.style.transform =
    zoomLevel === 1.0 ? "" : `scale(${zoomLevel})`;
  grid.style.transformOrigin = "top left";
  const viewport = grid.closest<HTMLElement>(".map-viewport");
  if (viewport) {
    viewport.style.overflow = zoomLevel > 1.0 ? "auto" : "";
  }
}

function wireGestureZoom(grid: HTMLElement): void {
  const viewport = grid.closest<HTMLElement>(".map-viewport");
  const target = viewport ?? grid;
  const existing = gestureBindings.get(grid);
  if (existing?.target === target) return;
  existing?.cleanup();

  // Wheel zoom: Ctrl+wheel zooms, plain wheel scrolls normally
  const onWheel = (e: WheelEvent) => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    const delta = e.deltaY > 0 ? -ZOOM_STEP : ZOOM_STEP;
    zoomLevel = clampZoom(zoomLevel + delta);
    applyZoom(grid);
  };
  target.addEventListener("wheel", onWheel, { passive: false });

  // Pinch-to-zoom via pointer events
  const pointers = new Map<number, PointerEvent>();

  function pointerDist(): number {
    const pts = [...pointers.values()];
    const a = pts[0];
    const b = pts[1];
    if (!a || !b) return 0;
    return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
  }

  let pinchStartDist = 0;
  let pinchStartZoom = 1.0;

  const onPointerDown = (e: PointerEvent) => {
    if (e.pointerType !== "touch") return;
    pointers.set(e.pointerId, e);
    if (pointers.size === 2) {
      pinchStartDist = pointerDist();
      pinchStartZoom = zoomLevel;
      try {
        target.setPointerCapture(e.pointerId);
      } catch {
        // Ignore capture failures; gesture handling still works without it.
      }
    }
  };
  target.addEventListener("pointerdown", onPointerDown);

  const onPointerMove = (e: PointerEvent) => {
    if (!pointers.has(e.pointerId)) return;
    pointers.set(e.pointerId, e);
    if (pointers.size === 2 && pinchStartDist > 0) {
      const ratio = pointerDist() / pinchStartDist;
      zoomLevel = clampZoom(pinchStartZoom * ratio);
      applyZoom(grid);
    }
  };
  target.addEventListener("pointermove", onPointerMove);

  const endPointer = (e: PointerEvent) => {
    pointers.delete(e.pointerId);
  };
  target.addEventListener("pointerup", endPointer);
  target.addEventListener("pointercancel", endPointer);

  // Prevent browser's native pinch-zoom on the map area
  const onTouchMove = (e: TouchEvent) => {
    if (e.touches.length >= 2) e.preventDefault();
  };
  target.addEventListener("touchmove", onTouchMove, { passive: false });

  gestureBindings.set(grid, {
    target,
    cleanup: () => {
      target.removeEventListener("wheel", onWheel);
      target.removeEventListener("pointerdown", onPointerDown);
      target.removeEventListener("pointermove", onPointerMove);
      target.removeEventListener("pointerup", endPointer);
      target.removeEventListener("pointercancel", endPointer);
      target.removeEventListener("touchmove", onTouchMove);
    },
  });
}

function ensureZoomControls(grid: HTMLElement): void {
  wireGestureZoom(grid);
  const stage = grid.closest<HTMLElement>(".map-stage");
  if (!stage) return;
  let controls = zoomControlsByGrid.get(grid) ?? null;
  if (controls && controls.parentElement !== stage) {
    controls.remove();
    controls = null;
  }
  if (!controls) {
    controls = document.createElement("div");
    controls.className = "map-zoom-controls";

    const btnIn = document.createElement("button");
    btnIn.type = "button";
    btnIn.textContent = "+";
    btnIn.setAttribute("aria-label", t("map.zoom_in"));
    btnIn.addEventListener("click", () => {
      zoomLevel = clampZoom(zoomLevel + ZOOM_STEP);
      applyZoom(grid);
    });

    const btnOut = document.createElement("button");
    btnOut.type = "button";
    btnOut.textContent = "\u2212";
    btnOut.setAttribute("aria-label", t("map.zoom_out"));
    btnOut.addEventListener("click", () => {
      zoomLevel = clampZoom(zoomLevel - ZOOM_STEP);
      applyZoom(grid);
    });

    const btnReset = document.createElement("button");
    btnReset.type = "button";
    btnReset.textContent = "1:1";
    btnReset.setAttribute("aria-label", t("map.zoom_reset"));
    btnReset.addEventListener("click", () => {
      zoomLevel = 1.0;
      applyZoom(grid);
    });

    controls.append(btnIn, btnOut, btnReset);
    stage.appendChild(controls);
    zoomControlsByGrid.set(grid, controls);
  }
  applyZoom(grid);
}

function plotIdTextLength(label: string): string {
  if (label.length <= 2) return "58";
  if (label.length === 3) return "70";
  return "80";
}

function buildPlotIdLabel(label: string): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.classList.add("plot-label", "plot-label-svg");
  svg.dataset["labelKind"] = "plot-id";
  svg.dataset["labelLength"] = String(label.length);
  svg.setAttribute("viewBox", "0 0 100 100");
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");

  const text = document.createElementNS(SVG_NS, "text");
  text.classList.add("plot-label-svg-text");
  text.setAttribute("x", "50");
  text.setAttribute("y", "53");
  text.setAttribute("text-anchor", "middle");
  text.setAttribute("dominant-baseline", "middle");
  text.setAttribute("textLength", plotIdTextLength(label));
  text.setAttribute("lengthAdjust", "spacingAndGlyphs");
  text.textContent = label;
  svg.appendChild(text);
  return svg;
}

function buildIconLabel(label: string): HTMLSpanElement {
  const labelEl = document.createElement("span");
  labelEl.className = "plot-label plot-label-icon";
  labelEl.dataset["labelKind"] = "icon";
  labelEl.textContent = label;
  return labelEl;
}

interface RenderMapParams {
  grid: HTMLElement;
  plots: Plot[];
  gridRows: number;
  gridCols: number;
  selectedPlotIds: Set<string>;
  highlightedPlotIds: Set<string>;
  sunlitPlotIds: Set<string>;
  elevationData: Record<string, number> | null;
  elevationRange: { min: number; max: number } | null;
  showElevation: boolean;
  editMode: boolean;
  housePosition: { row: number; col: number };
  houseSize: { width: number; height: number };
  northDegrees: number;
  onPlotClick: (plot: Plot, event: MouseEvent) => void;
  onPlotContextMenu: (plot: Plot, x: number, y: number) => void;
  onPlotDragStart: (plot: Plot, event: DragEvent) => void;
  onPlotDragEnd: () => void;
  onDragOverCell: (targetRow: number, targetCol: number) => void;
  onDropToCell: (targetRow: number, targetCol: number, targetPlotId?: string, event?: DragEvent) => void;
  onExtendPlot: (plot: Plot) => void;
  onEmptyCellClick: (row: number, col: number) => void;
  onHouseMoveStart: (event: MouseEvent) => void;
  onHouseResizeStart: (event: MouseEvent) => void;
  onHouseClick?: () => void;
}

function cellData(el: EventTarget | null): { row: number; col: number } | null {
  const target = (el as HTMLElement | null)?.closest<HTMLElement>("[data-row][data-col]");
  if (!target) return null;
  const row = Number(target.dataset["row"]);
  const col = Number(target.dataset["col"]);
  return Number.isFinite(row) && Number.isFinite(col) ? { row, col } : null;
}

export function renderMapGrid(params: RenderMapParams): void {
  const {
    grid,
    plots,
    selectedPlotIds,
    highlightedPlotIds,
    sunlitPlotIds,
    elevationData,
    elevationRange,
    showElevation,
    editMode,
    housePosition,
    houseSize,
    northDegrees,
    onPlotClick,
    onPlotContextMenu,
    onPlotDragStart,
    onPlotDragEnd,
    onDragOverCell,
    onDropToCell,
    onExtendPlot,
    onEmptyCellClick,
    onHouseMoveStart,
    onHouseResizeStart,
  } = params;

  grid.replaceChildren();
  grid.dataset["northDegrees"] = String(northDegrees);
  const gridLabel = `${params.gridCols}m × ${params.gridRows}m`;
  grid.dataset["gridLabel"] = gridLabel;
  const camera = grid.parentElement;
  if (camera instanceof HTMLElement && camera.classList.contains("map-camera")) {
    camera.dataset["gridLabel"] = gridLabel;
  }

  const byCell = new Map<string, Plot>();
  for (const p of plots) {
    byCell.set(`${p.grid_row},${p.grid_col}`, p);
  }

  const fragment = document.createDocumentFragment();

  grid.style.setProperty("--grid-rows", String(params.gridRows));
  grid.style.setProperty("--grid-cols", String(params.gridCols));

  for (let row = 1; row <= params.gridRows; row++) {
    for (let col = 1; col <= params.gridCols; col++) {
      const plot = byCell.get(`${row},${col}`);

      if (plot) {
        fragment.appendChild(
          buildPlotCell(
            plot, row, col, editMode,
            selectedPlotIds, highlightedPlotIds, sunlitPlotIds,
            showElevation, elevationData, elevationRange,
          ),
        );
      } else if (editMode) {
        const empty = document.createElement("div");
        empty.className = "empty-cell";
        empty.dataset["row"] = String(row);
        empty.dataset["col"] = String(col);
        empty.style.gridRow = String(row);
        empty.style.gridColumn = String(col);
        fragment.appendChild(empty);
      }
    }
  }

  grid.appendChild(fragment);

  wireGridDelegation(grid, byCell, {
    editMode, onPlotClick, onPlotContextMenu, onPlotDragStart,
    onPlotDragEnd, onDragOverCell, onDropToCell, onExtendPlot,
    onEmptyCellClick,
  });

  renderHouse(
    grid, housePosition, houseSize, editMode,
    onHouseMoveStart, onHouseResizeStart, params.onHouseClick,
  );

  ensureZoomControls(grid);
}

function buildPlotCell(
  plot: Plot,
  row: number,
  col: number,
  editMode: boolean,
  selectedPlotIds: Set<string>,
  highlightedPlotIds: Set<string>,
  sunlitPlotIds: Set<string>,
  showElevation: boolean,
  elevationData: Record<string, number> | null,
  elevationRange: { min: number; max: number } | null,
): HTMLElement {
  const el = document.createElement("div");
  el.className = "plot";
  el.dataset["plotId"] = plot.plot_id;
  el.dataset["zone"] = plot.zone_code;
  el.dataset["row"] = String(row);
  el.dataset["col"] = String(col);
  el.style.gridRow = String(row);
  el.style.gridColumn = String(col);

  const count = plot.plant_count;
  const densityLevel = count === 0 ? 0 : count <= 2 ? 1 : count <= 5 ? 2 : 3;
  const label = plot.has_tree ? "🌳" : plot.has_bush ? "🌿" : plot.plot_id;

  el.appendChild(label === plot.plot_id ? buildPlotIdLabel(label) : buildIconLabel(label));

  if (densityLevel > 0) {
    const densityEl = document.createElement("span");
    densityEl.className = "plot-density";
    densityEl.dataset["density"] = String(densityLevel);
    densityEl.setAttribute("aria-hidden", "true");
    for (let i = 0; i < densityLevel; i += 1) {
      const dot = document.createElement("span");
      dot.className = "plot-density-dot";
      densityEl.appendChild(dot);
    }
    el.appendChild(densityEl);
  }

  if (editMode) {
    const extBtn = document.createElement("button");
    extBtn.className = "plot-extend-btn";
    extBtn.title = t("map.add_next_plot");
    extBtn.type = "button";
    extBtn.textContent = "+";
    el.appendChild(extBtn);
  }

  el.title = t("map.plot_tooltip", { zone: plot.zone_name, number: plot.plot_number, count: plot.plant_count });
  el.draggable = editMode;

  let elevActive = false;
  if (showElevation && elevationData && elevationRange) {
    const elev = elevationData[plot.plot_id];
    if (elev !== undefined) {
      const span = elevationRange.max - elevationRange.min;
      const ratio = span > 0 ? (elev - elevationRange.min) / span : 0.5;
      const hue = Math.round(240 * (1 - ratio));
      el.classList.add("elevation-overlay");
      el.style.setProperty("--elev-hue", String(hue));
      el.title += ` · ${t("map.elevation_value", { value: elev.toFixed(1) })}`;
      elevActive = true;
    }
  }

  if (!elevActive && plot.color) {
    el.style.background = plot.color;
  }
  if (selectedPlotIds.has(plot.plot_id)) {
    el.classList.add("multi-selected");
  }
  if (highlightedPlotIds.has(plot.plot_id)) {
    el.classList.add("highlighted");
  }
  if (sunlitPlotIds.has(plot.plot_id)) {
    el.classList.add("sunlit-direct");
    el.dataset["sunlight"] = "sun";
  }

  return el;
}

export function applyPlotIndicators(
  grid: HTMLElement,
  alerts: {
    task_plots: Set<string>;
    issue_plots: Set<string>;
    frost_plots: Set<string>;
  },
): void {
  for (const cell of grid.querySelectorAll<HTMLElement>(
    ".plot[data-plot-id]",
  )) {
    const existing = cell.querySelector(".plot-indicators");
    if (existing) existing.remove();

    const plotId = cell.dataset["plotId"] ?? "";
    const types: string[] = [];
    if (alerts.task_plots.has(plotId)) types.push("task");
    if (alerts.issue_plots.has(plotId)) types.push("issue");
    if (alerts.frost_plots.has(plotId)) types.push("frost");
    if (types.length === 0) continue;

    const wrapper = document.createElement("div");
    wrapper.className = "plot-indicators";
    for (const type of types) {
      const dot = document.createElement("span");
      dot.className = `plot-indicator plot-indicator--${type}`;
      wrapper.appendChild(dot);
    }
    cell.appendChild(wrapper);
  }
}

export function syncSelectedPlots(
  grid: HTMLElement,
  selectedPlotIds: ReadonlySet<string>,
): void {
  for (const cell of grid.querySelectorAll<HTMLElement>(".plot[data-plot-id]")) {
    const plotId = cell.dataset["plotId"] ?? "";
    cell.classList.toggle("multi-selected", selectedPlotIds.has(plotId));
  }
}

interface GridCallbacks {
  editMode: boolean;
  onPlotClick: (plot: Plot, event: MouseEvent) => void;
  onPlotContextMenu: (plot: Plot, x: number, y: number) => void;
  onPlotDragStart: (plot: Plot, event: DragEvent) => void;
  onPlotDragEnd: () => void;
  onDragOverCell: (targetRow: number, targetCol: number) => void;
  onDropToCell: (targetRow: number, targetCol: number, targetPlotId?: string, event?: DragEvent) => void;
  onExtendPlot: (plot: Plot) => void;
  onEmptyCellClick: (row: number, col: number) => void;
}

function wireGridDelegation(
  grid: HTMLElement,
  byCell: Map<string, Plot>,
  cbs: GridCallbacks,
): void {
  const existing = gridDelegationState.get(grid);
  if (existing) {
    existing.byCell = byCell;
    existing.callbacks = cbs;
    return;
  }

  const state = {
    byCell,
    callbacks: cbs,
  };
  gridDelegationState.set(grid, state);

  const plotAt = (el: EventTarget | null): Plot | undefined => {
    const plotEl = (el as HTMLElement | null)?.closest<HTMLElement>(".plot");
    const id = plotEl?.dataset["plotId"];
    if (!id) return undefined;
    const r = Number(plotEl.dataset["row"]);
    const c = Number(plotEl.dataset["col"]);
    return state.byCell.get(`${r},${c}`);
  };

  grid.addEventListener("click", (e) => {
    const extBtn = (e.target as HTMLElement).closest<HTMLElement>(".plot-extend-btn");
    if (extBtn) {
      e.stopPropagation();
      const plot = plotAt(extBtn);
      if (plot) state.callbacks.onExtendPlot(plot);
      return;
    }
    const plot = plotAt(e.target);
    if (plot) {
      state.callbacks.onPlotClick(plot, e);
      return;
    }
    if (state.callbacks.editMode) {
      const cell = cellData(e.target);
      if (cell) state.callbacks.onEmptyCellClick(cell.row, cell.col);
    }
  });

  grid.addEventListener("contextmenu", (e) => {
    const plot = plotAt(e.target);
    if (plot) {
      e.preventDefault();
      state.callbacks.onPlotContextMenu(plot, e.clientX, e.clientY);
    }
  });

  grid.addEventListener("dragstart", (e) => {
    const plot = plotAt(e.target);
    if (plot) state.callbacks.onPlotDragStart(plot, e);
  });

  grid.addEventListener("dragend", () => state.callbacks.onPlotDragEnd());

  grid.addEventListener("dragover", (e) => {
    const cell = cellData(e.target);
    if (!cell) return;
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
    state.callbacks.onDragOverCell(cell.row, cell.col);
  });

  grid.addEventListener("drop", (e) => {
    const cell = cellData(e.target);
    if (!cell) return;
    e.preventDefault();
    const plot = plotAt(e.target);
    state.callbacks.onDropToCell(cell.row, cell.col, plot?.plot_id, e);
  });
}

function renderHouse(
  grid: HTMLElement,
  housePosition: { row: number; col: number },
  houseSize: { width: number; height: number },
  editMode: boolean,
  onHouseMoveStart: (event: MouseEvent) => void,
  onHouseResizeStart: (event: MouseEvent) => void,
  onHouseClick?: () => void,
): void {
  const house = document.createElement("div");
  house.className = "house-placeholder";
  house.id = "house";

  const houseWidth = houseSize.width;
  const houseHeight = houseSize.height;

  house.style.gridRow = `${housePosition.row} / ${housePosition.row + houseHeight}`;
  house.style.gridColumn = `${housePosition.col} / ${housePosition.col + houseWidth}`;
  const label = document.createElement("div");
  label.className = "house-label";
  const strong = document.createElement("strong");
  strong.textContent = t("map.house");
  const span = document.createElement("span");
  span.textContent = `${houseWidth}m × ${houseHeight}m`;
  label.append(strong, span);
  house.appendChild(label);
  house.title = editMode
    ? t("map.house_edit_hint")
    : t("map.house_size", { width: houseWidth, height: houseHeight });

  if (editMode) {
    house.addEventListener("mousedown", (e) => {
      // Let the resize handle handle its own mousedown
      if ((e.target as HTMLElement).closest(".house-resize-handle")) return;
      e.preventDefault();
      onHouseMoveStart(e);
    });

    const resizeHandle = document.createElement("button");
    resizeHandle.type = "button";
    resizeHandle.className = "house-resize-handle";
    resizeHandle.title = t("map.house_resize_hint");
    resizeHandle.setAttribute("aria-label", t("map.house_resize_hint"));
    resizeHandle.addEventListener("mousedown", (e) => {
      e.preventDefault();
      e.stopPropagation();
      onHouseResizeStart(e);
    });
    house.appendChild(resizeHandle);
  }

  if (!editMode && onHouseClick) {
    house.classList.add("house--clickable");
    house.addEventListener("click", (e) => {
      e.preventDefault();
      onHouseClick();
    });
  }

  grid.appendChild(house);
}
