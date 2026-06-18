import {
  HOUSE_MIN_HEIGHT,
  HOUSE_MIN_WIDTH,
  UNDO_STACK_LIMIT,
} from "../core/constants";
import type { AppState, MoveAction } from "../core/models";
import { batchMovePlotsApi, getApiErrorMessage } from "../services/api";
import { showToast } from "./toast";
import { t } from "../core/i18n";

export interface EditCallbacks {
  renderPlots: () => void;
  fetchPlots: () => Promise<void>;
  persistHouse: () => Promise<void>;
}

export function toggleEditMode(
  state: AppState,
  cbs: EditCallbacks,
): void {
  state.editMode = !state.editMode;
  if (!state.editMode) {
    state.selectedPlotIds.clear();
    state.undoStack = [];
  }

  const btn = document.getElementById("edit-mode-btn");
  const selectAllBtn = document.getElementById("select-all-btn");
  const clearBtn = document.getElementById("clear-selection-btn");
  const countSpan = document.getElementById("selection-count");
  const undoBtn = document.getElementById("undo-btn");
  const editContextBar = document.getElementById("map-edit-context-bar");

  if (btn) {
    btn.textContent = t("map.edit");
    btn.classList.toggle("active", state.editMode);
    btn.setAttribute("aria-pressed", state.editMode ? "true" : "false");
  }
  if (selectAllBtn) {
    selectAllBtn.style.display = state.editMode ? "block" : "none";
  }
  if (clearBtn) {
    clearBtn.style.display = state.editMode ? "block" : "none";
  }
  if (countSpan) {
    countSpan.style.display = state.editMode ? "block" : "none";
  }
  if (undoBtn) {
    undoBtn.style.display = state.editMode ? "block" : "none";
    (undoBtn as HTMLButtonElement).disabled =
      state.undoStack.length === 0;
  }
  if (editContextBar instanceof HTMLElement) {
    editContextBar.hidden = !state.editMode;
  }

  updateSelectionCount(state);
  cbs.renderPlots();
}

export function updateSelectionCount(state: AppState): void {
  const countSpan = document.getElementById("selection-count");
  if (!countSpan) return;

  if (state.selectedPlotIds.size > 0) {
    countSpan.textContent = t("map.selected_count", { count: state.selectedPlotIds.size });
    countSpan.style.display = "block";
  } else {
    countSpan.textContent = "";
  }
}

export function togglePlotSelection(
  state: AppState,
  plotId: string,
  cbs: EditCallbacks,
): void {
  if (state.selectedPlotIds.has(plotId)) {
    state.selectedPlotIds.delete(plotId);
  } else {
    state.selectedPlotIds.add(plotId);
  }
  updateSelectionCount(state);
  cbs.renderPlots();
}

export function selectPlotRange(
  state: AppState,
  endPlotId: string,
  cbs: EditCallbacks,
): void {
  if (state.selectedPlotIds.size === 0) return;

  const startPlotId = Array.from(state.selectedPlotIds)[0];
  const startPlot = state.plots.find(
    (p) => p.plot_id === startPlotId,
  );
  const endPlot = state.plots.find((p) => p.plot_id === endPlotId);
  if (!startPlot || !endPlot) return;
  if (startPlot.grid_row == null || startPlot.grid_col == null) return;
  if (endPlot.grid_row == null || endPlot.grid_col == null) return;

  const minRow = Math.min(startPlot.grid_row, endPlot.grid_row);
  const maxRow = Math.max(startPlot.grid_row, endPlot.grid_row);
  const minCol = Math.min(startPlot.grid_col, endPlot.grid_col);
  const maxCol = Math.max(startPlot.grid_col, endPlot.grid_col);

  state.plots.forEach((plot) => {
    if (
      plot.grid_row != null &&
      plot.grid_col != null &&
      plot.grid_row >= minRow &&
      plot.grid_row <= maxRow &&
      plot.grid_col >= minCol &&
      plot.grid_col <= maxCol
    ) {
      state.selectedPlotIds.add(plot.plot_id);
    }
  });

  updateSelectionCount(state);
  cbs.renderPlots();
}

export function selectAll(
  state: AppState,
  cbs: EditCallbacks,
): void {
  if (!state.editMode) return;
  state.plots.forEach((plot) =>
    state.selectedPlotIds.add(plot.plot_id),
  );
  updateSelectionCount(state);
  cbs.renderPlots();
}

export function clearSelection(
  state: AppState,
  cbs: EditCallbacks,
): void {
  state.selectedPlotIds.clear();
  updateSelectionCount(state);
  cbs.renderPlots();
}

export function updateUndoButton(state: AppState): void {
  const undoBtn = document.getElementById(
    "undo-btn",
  ) as HTMLButtonElement | null;
  if (undoBtn) undoBtn.disabled = state.undoStack.length === 0;
}

export function moveHouse(
  state: AppState,
  rowOffset: number,
  colOffset: number,
  cbs: EditCallbacks,
): void {
  const newRow = state.housePosition.row + rowOffset;
  const newCol = state.housePosition.col + colOffset;

  const houseWidth = state.houseSize.width;
  const houseHeight = state.houseSize.height;

  if (
    newRow < 1 ||
    newRow + houseHeight - 1 > state.gridRows ||
    newCol < 1 ||
    newCol + houseWidth - 1 > state.gridCols
  ) {
    showToast(t("map.house_outside_grid"), "error");
    return;
  }

  const action: MoveAction = {
    plots: [],
    house: {
      row: state.housePosition.row,
      col: state.housePosition.col,
      width: state.houseSize.width,
      height: state.houseSize.height,
    },
  };
  state.undoStack.push(action);
  if (state.undoStack.length > UNDO_STACK_LIMIT) {
    state.undoStack.shift();
  }
  updateUndoButton(state);

  state.housePosition = { row: newRow, col: newCol };
  void cbs.persistHouse();
  cbs.renderPlots();
}

export function clampHouseSize(
  state: AppState,
  width: number,
  height: number,
): { width: number; height: number } {
  return {
    width: Math.max(
      HOUSE_MIN_WIDTH,
      Math.min(width, state.gridCols - state.housePosition.col + 1),
    ),
    height: Math.max(
      HOUSE_MIN_HEIGHT,
      Math.min(height, state.gridRows - state.housePosition.row + 1),
    ),
  };
}

export async function moveSelectedPlots(
  state: AppState,
  rowOffset: number,
  colOffset: number,
  cbs: EditCallbacks,
): Promise<void> {
  const plotsToMove = Array.from(state.selectedPlotIds)
    .map((id) => {
      const plot = state.plots.find((p) => p.plot_id === id);
      if (!plot || plot.grid_row == null || plot.grid_col == null) return null;
      return {
        plot_id: id,
        oldRow: plot.grid_row,
        oldCol: plot.grid_col,
        newRow: plot.grid_row + rowOffset,
        newCol: plot.grid_col + colOffset,
      };
    })
    .filter(Boolean);

  const invalidPlots = plotsToMove.filter(
    (p) =>
      p &&
      (p.newRow < 1 ||
        p.newRow > state.gridRows ||
        p.newCol < 1 ||
        p.newCol > state.gridCols),
  );

  if (invalidPlots.length > 0) {
    showToast(t("map.plots_outside_grid"), "error");
    return;
  }

  const action: MoveAction = {
    plots: plotsToMove
      .filter(
        (p): p is NonNullable<typeof p> => p !== null,
      )
      .map((p) => ({
        plot_id: p.plot_id,
        row: p.oldRow,
        col: p.oldCol,
      })),
  };

  try {
    const moves = plotsToMove
      .filter(
        (p): p is NonNullable<typeof p> => p !== null,
      )
      .map((p) => ({
        plot_id: p.plot_id,
        grid_row: p.newRow,
        grid_col: p.newCol,
      }));
    await batchMovePlotsApi(moves);
    state.undoStack.push(action);
    if (state.undoStack.length > UNDO_STACK_LIMIT) {
      state.undoStack.shift();
    }
    updateUndoButton(state);
  } catch (err) {
    showToast(getApiErrorMessage(err), "error");
  }

  await cbs.fetchPlots();
}

export async function undo(
  state: AppState,
  cbs: EditCallbacks,
): Promise<void> {
  const action = state.undoStack.pop();
  if (!action) return;

  if (action.house) {
    state.housePosition = {
      row: action.house.row,
      col: action.house.col,
    };
    state.houseSize = {
      width: action.house.width,
      height: action.house.height,
    };
    void cbs.persistHouse();
  }

  if (action.plots.length > 0) {
    try {
      const moves = action.plots.map((p) => ({
        plot_id: p.plot_id,
        grid_row: p.row,
        grid_col: p.col,
      }));
      await batchMovePlotsApi(moves);
    } catch (err) {
      showToast(getApiErrorMessage(err), "error");
    }
  }

  updateUndoButton(state);
  await cbs.fetchPlots();
}
