import {
  MINIMAP_HEIGHT,
  MINIMAP_WIDTH,
  ZOOM_MAX,
  ZOOM_MIN,
  ZOOM_STEP,
} from "../core/constants";
import type { CameraState, Plot } from "../core/models";

export interface CameraCallbacks {
  onTransformChange: (state: CameraState) => void;
}

export interface CameraController {
  getState: () => CameraState;
  setState: (state: CameraState) => void;
  fitAll: () => void;
  setGridDims: (rows: number, cols: number) => void;
  updateMinimap: (plots: Plot[], hiddenZones: Set<string>) => void;
  destroy: () => void;
}

export function initCamera(
  viewport: HTMLElement,
  camera: HTMLElement,
  callbacks: CameraCallbacks,
  gridRows = 30,
  gridCols = 22,
): CameraController {
  let state: CameraState = { x: 0, y: 0, zoom: 1 };
  let _gridRows = gridRows;
  let _gridCols = gridCols;
  let isPanning = false;
  let panStart = { x: 0, y: 0 };
  let panOrigin = { x: 0, y: 0 };
  let spaceHeld = false;

  function applyTransform(): void {
    camera.style.transform =
      `translate(${state.x}px, ${state.y}px) scale(${state.zoom})`;
    callbacks.onTransformChange(state);
  }

  function clampZoom(z: number): number {
    return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));
  }

  function zoomAt(
    clientX: number,
    clientY: number,
    newZoom: number,
  ): void {
    const rect = viewport.getBoundingClientRect();
    const px = clientX - rect.left;
    const py = clientY - rect.top;

    const clamped = clampZoom(newZoom);
    const ratio = clamped / state.zoom;
    state.x = px - ratio * (px - state.x);
    state.y = py - ratio * (py - state.y);
    state.zoom = clamped;
    applyTransform();
  }

  function isCameraPanTarget(target: EventTarget | null): boolean {
    if (!target || !(target instanceof HTMLElement)) return false;
    if (spaceHeld) return true;
    return (
      !target.closest(".plot") &&
      !target.closest(".empty-cell") &&
      !target.closest(".house-placeholder")
    );
  }

  function onWheel(e: WheelEvent): void {
    e.preventDefault();
    const delta = e.deltaY > 0 ? -ZOOM_STEP : ZOOM_STEP;
    zoomAt(e.clientX, e.clientY, state.zoom + delta);
  }

  function onPointerDown(e: PointerEvent): void {
    if (e.button !== 0) return;
    if (!isCameraPanTarget(e.target)) return;

    isPanning = true;
    panStart = { x: e.clientX, y: e.clientY };
    panOrigin = { x: state.x, y: state.y };
    viewport.setPointerCapture(e.pointerId);
    viewport.style.cursor = "grabbing";
    e.preventDefault();
  }

  function onPointerMove(e: PointerEvent): void {
    if (!isPanning) return;
    state.x = panOrigin.x + (e.clientX - panStart.x);
    state.y = panOrigin.y + (e.clientY - panStart.y);
    applyTransform();
  }

  function onPointerUp(e: PointerEvent): void {
    if (!isPanning) return;
    isPanning = false;
    viewport.releasePointerCapture(e.pointerId);
    viewport.style.cursor = "";
  }

  function onDblClick(e: MouseEvent): void {
    if (
      e.target instanceof HTMLElement &&
      (e.target.closest(".plot") ||
        e.target.closest(".empty-cell") ||
        e.target.closest(".house-placeholder"))
    ) {
      return;
    }
    zoomAt(e.clientX, e.clientY, state.zoom + ZOOM_STEP * 3);
  }

  function onKeyDown(e: KeyboardEvent): void {
    const el = e.target;
    if (
      el instanceof HTMLInputElement ||
      el instanceof HTMLTextAreaElement ||
      el instanceof HTMLSelectElement
    ) {
      return;
    }
    if (e.key === " " && !spaceHeld) {
      spaceHeld = true;
    }
    if (e.ctrlKey && e.key === "0") {
      e.preventDefault();
      fitAll();
    }
  }

  function onKeyUp(e: KeyboardEvent): void {
    if (e.key === " ") {
      spaceHeld = false;
    }
  }

  function onDragStart(e: DragEvent): void {
    if (spaceHeld) {
      e.preventDefault();
    }
  }

  let lastTouchDist = 0;
  let lastTouchCenter = { x: 0, y: 0 };

  function getTouchDist(t1: Touch, t2: Touch): number {
    const dx = t1.clientX - t2.clientX;
    const dy = t1.clientY - t2.clientY;
    return Math.sqrt(dx * dx + dy * dy);
  }

  function getTouchCenter(t1: Touch, t2: Touch): { x: number; y: number } {
    return {
      x: (t1.clientX + t2.clientX) / 2,
      y: (t1.clientY + t2.clientY) / 2,
    };
  }

  function onTouchStart(e: TouchEvent): void {
    if (e.touches.length === 2) {
      const t1 = e.touches[0]!;
      const t2 = e.touches[1]!;
      lastTouchDist = getTouchDist(t1, t2);
      lastTouchCenter = getTouchCenter(t1, t2);
      e.preventDefault();
    }
  }

  function onTouchMove(e: TouchEvent): void {
    if (e.touches.length === 2) {
      const t1 = e.touches[0]!;
      const t2 = e.touches[1]!;
      const dist = getTouchDist(t1, t2);
      const center = getTouchCenter(t1, t2);
      const scale = dist / lastTouchDist;
      zoomAt(center.x, center.y, state.zoom * scale);
      state.x += center.x - lastTouchCenter.x;
      state.y += center.y - lastTouchCenter.y;
      applyTransform();
      lastTouchDist = dist;
      lastTouchCenter = center;
      e.preventDefault();
    }
  }

  function fitAll(): void {
    const gridEl = camera.querySelector(".map-grid");
    if (!gridEl) return;
    const vRect = viewport.getBoundingClientRect();
    const gRect = gridEl.getBoundingClientRect();
    const currentScale = state.zoom;
    const naturalW = gRect.width / currentScale;
    const naturalH = gRect.height / currentScale;
    const padding = 32;
    const scaleX = (vRect.width - padding * 2) / naturalW;
    const scaleY = (vRect.height - padding * 2) / naturalH;
    const zoom = clampZoom(Math.min(scaleX, scaleY));
    const cx = (vRect.width - naturalW * zoom) / 2;
    const cy = (vRect.height - naturalH * zoom) / 2;
    state = { x: cx, y: cy, zoom };
    applyTransform();
  }

  // ── Minimap ──────────────────────────────────────────────
  const minimapWrap = document.createElement("div");
  minimapWrap.className = "minimap";
  const minimapCanvas = document.createElement("canvas");
  minimapCanvas.width = MINIMAP_WIDTH;
  minimapCanvas.height = MINIMAP_HEIGHT;
  minimapWrap.appendChild(minimapCanvas);
  viewport.appendChild(minimapWrap);

  const ZONE_COLORS: Record<string, string> = {
    B: "#6dbb6d", V: "#a87fc4", T: "#e0826a", R: "#d4c044",
    S: "#c8c480", P: "#58bfb0", D: "#5a9fd4", H: "#d0789a",
  };

  let cachedPlots: Plot[] = [];
  let cachedHidden = new Set<string>();

  function updateMinimapVisibility(): void {
    minimapWrap.style.display = state.zoom > 1.05 ? "" : "none";
  }

  function drawMinimap(): void {
    updateMinimapVisibility();
    const ctx = minimapCanvas.getContext("2d");
    if (!ctx) return;

    const cw = MINIMAP_WIDTH;
    const ch = MINIMAP_HEIGHT;
    const cellW = cw / _gridCols;
    const cellH = ch / _gridRows;

    ctx.clearRect(0, 0, cw, ch);
    ctx.fillStyle = "rgba(0,0,0,0.15)";
    ctx.fillRect(0, 0, cw, ch);

    for (const plot of cachedPlots) {
      if (plot.grid_row == null || plot.grid_col == null) continue;
      const dimmed = cachedHidden.has(plot.zone_code);
      const color = ZONE_COLORS[plot.zone_code] ?? "#888";
      ctx.globalAlpha = dimmed ? 0.15 : 0.85;
      ctx.fillStyle = color;
      ctx.fillRect(
        (plot.grid_col - 1) * cellW,
        (plot.grid_row - 1) * cellH,
        cellW - 0.5,
        cellH - 0.5,
      );
    }

    ctx.globalAlpha = 0.4;
    ctx.fillStyle = "#888";
    ctx.fillRect(5 * cellW, 8 * cellH, 12 * cellW, 8 * cellH);
    ctx.globalAlpha = 1;

    const gridEl = camera.querySelector(".map-grid");
    if (!gridEl) return;
    const vRect = viewport.getBoundingClientRect();
    const gRect = gridEl.getBoundingClientRect();
    const naturalW = gRect.width / state.zoom;
    const naturalH = gRect.height / state.zoom;
    if (naturalW <= 0 || naturalH <= 0) return;

    const scaleX = cw / naturalW;
    const scaleY = ch / naturalH;

    const vx = (-state.x / state.zoom) * scaleX;
    const vy = (-state.y / state.zoom) * scaleY;
    const vw = (vRect.width / state.zoom) * scaleX;
    const vh = (vRect.height / state.zoom) * scaleY;

    ctx.strokeStyle = "rgba(255,255,255,0.9)";
    ctx.lineWidth = 1.5;
    ctx.strokeRect(vx, vy, vw, vh);
  }

  function onMinimapPointer(e: PointerEvent): void {
    const gridEl = camera.querySelector(".map-grid");
    if (!gridEl) return;
    const rect = minimapCanvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const vRect = viewport.getBoundingClientRect();
    const gRect = gridEl.getBoundingClientRect();
    const naturalW = gRect.width / state.zoom;
    const naturalH = gRect.height / state.zoom;
    if (naturalW <= 0 || naturalH <= 0) return;

    const targetX = (mx / MINIMAP_WIDTH) * naturalW;
    const targetY = (my / MINIMAP_HEIGHT) * naturalH;

    state.x = -(targetX - vRect.width / state.zoom / 2) * state.zoom;
    state.y = -(targetY - vRect.height / state.zoom / 2) * state.zoom;
    applyTransform();
  }

  let minimapDragging = false;

  function onMinimapDown(e: PointerEvent): void {
    minimapDragging = true;
    minimapCanvas.setPointerCapture(e.pointerId);
    onMinimapPointer(e);
    e.stopPropagation();
  }
  function onMinimapMove(e: PointerEvent): void {
    if (minimapDragging) onMinimapPointer(e);
  }
  function onMinimapUp(e: PointerEvent): void {
    minimapDragging = false;
    minimapCanvas.releasePointerCapture(e.pointerId);
  }

  minimapCanvas.addEventListener("pointerdown", onMinimapDown);
  minimapCanvas.addEventListener("pointermove", onMinimapMove);
  minimapCanvas.addEventListener("pointerup", onMinimapUp);

  // ── Bind events ──────────────────────────────────────────
  viewport.addEventListener("wheel", onWheel, { passive: false });
  viewport.addEventListener("pointerdown", onPointerDown);
  viewport.addEventListener("pointermove", onPointerMove);
  viewport.addEventListener("pointerup", onPointerUp);
  viewport.addEventListener("pointercancel", onPointerUp);
  viewport.addEventListener("dblclick", onDblClick);
  viewport.addEventListener("touchstart", onTouchStart, { passive: false });
  viewport.addEventListener("touchmove", onTouchMove, { passive: false });
  camera.addEventListener("dragstart", onDragStart);
  window.addEventListener("keydown", onKeyDown);
  window.addEventListener("keyup", onKeyUp);

  return {
    getState: () => ({ ...state }),
    setState: (s: CameraState) => {
      state = { ...s };
      applyTransform();
    },
    fitAll,
    setGridDims: (rows: number, cols: number) => {
      _gridRows = rows;
      _gridCols = cols;
    },
    updateMinimap: (plots: Plot[], hiddenZones: Set<string>) => {
      cachedPlots = plots;
      cachedHidden = hiddenZones;
      drawMinimap();
    },
    destroy: () => {
      viewport.removeEventListener("wheel", onWheel);
      viewport.removeEventListener("pointerdown", onPointerDown);
      viewport.removeEventListener("pointermove", onPointerMove);
      viewport.removeEventListener("pointerup", onPointerUp);
      viewport.removeEventListener("pointercancel", onPointerUp);
      viewport.removeEventListener("dblclick", onDblClick);
      viewport.removeEventListener("touchstart", onTouchStart);
      viewport.removeEventListener("touchmove", onTouchMove);
      camera.removeEventListener("dragstart", onDragStart);
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      minimapCanvas.removeEventListener("pointerdown", onMinimapDown);
      minimapCanvas.removeEventListener("pointermove", onMinimapMove);
      minimapCanvas.removeEventListener("pointerup", onMinimapUp);
      minimapWrap.remove();
    },
  };
}
