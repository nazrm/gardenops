type OverlaysModule = typeof import("./overlays");

type ShowDeleteMenuParams = Parameters<OverlaysModule["showDeleteMenu"]>[0];
type ShowCreatePlotDialogParams = Parameters<OverlaysModule["showCreatePlotDialog"]>[0];
type ShowCreateZoneDialogParams = Parameters<OverlaysModule["showCreateZoneDialog"]>[0];
type ShowCreatePlantDialogParams = Parameters<OverlaysModule["showCreatePlantDialog"]>[0];
type ShowEditPlantDialogParams = Parameters<OverlaysModule["showEditPlantDialog"]>[0];
type ShowEditPlotDialogParams = Parameters<OverlaysModule["showEditPlotDialog"]>[0];
type ShowElevationEditorParams = Parameters<OverlaysModule["showElevationEditor"]>[0];

let overlaysModulePromise: Promise<OverlaysModule> | null = null;

function loadOverlaysModule(): Promise<OverlaysModule> {
  overlaysModulePromise ??= import("./overlays")
    .catch((err) => {
      overlaysModulePromise = null;
      throw err;
    });
  return overlaysModulePromise;
}

function reportOverlayLoadError(err: unknown): void {
  console.error("Failed to load garden dialog module", err);
}

export function showDeleteMenuLazy(params: ShowDeleteMenuParams): void {
  void loadOverlaysModule().then((mod) => mod.showDeleteMenu(params)).catch(reportOverlayLoadError);
}

export function showCreatePlotDialogLazy(params: ShowCreatePlotDialogParams): void {
  void loadOverlaysModule().then((mod) => mod.showCreatePlotDialog(params)).catch(reportOverlayLoadError);
}

export function showCreateZoneDialogLazy(params: ShowCreateZoneDialogParams): void {
  void loadOverlaysModule().then((mod) => mod.showCreateZoneDialog(params)).catch(reportOverlayLoadError);
}

export function showCreatePlantDialogLazy(params: ShowCreatePlantDialogParams): void {
  void loadOverlaysModule().then((mod) => mod.showCreatePlantDialog(params)).catch(reportOverlayLoadError);
}

export function showEditPlantDialogLazy(params: ShowEditPlantDialogParams): void {
  void loadOverlaysModule().then((mod) => mod.showEditPlantDialog(params)).catch(reportOverlayLoadError);
}

export function showEditPlotDialogLazy(params: ShowEditPlotDialogParams): void {
  void loadOverlaysModule().then((mod) => mod.showEditPlotDialog(params)).catch(reportOverlayLoadError);
}

export function showElevationEditorLazy(params: ShowElevationEditorParams): void {
  void loadOverlaysModule().then((mod) => mod.showElevationEditor(params)).catch(reportOverlayLoadError);
}
