import type { JournalEntry } from "../core/models";

type JournalPreviewModule = typeof import("./journalPreview");

let journalPreviewModulePromise: Promise<JournalPreviewModule> | null = null;

function loadJournalPreviewModule(): Promise<JournalPreviewModule> {
  journalPreviewModulePromise ??= import("./journalPreview")
    .catch((err) => {
      journalPreviewModulePromise = null;
      throw err;
    });
  return journalPreviewModulePromise;
}

export function renderPlotJournalPreviewLazy(
  container: HTMLElement,
  entries: JournalEntry[],
  onViewAll: () => void,
  isCurrent: () => boolean = () => container.isConnected,
): void {
  void loadJournalPreviewModule()
    .then((mod) => {
      if (!isCurrent()) return;
      mod.renderPlotJournalPreview(
        container,
        entries,
        onViewAll,
      );
    })
    .catch((err) => {
      console.error("Failed to load journal preview", err);
    });
}
