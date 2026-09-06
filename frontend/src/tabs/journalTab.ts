import type { AppContext } from "../core/appContext";
import { queryInput, querySelect } from "../core/dom";
import type { JournalEntry, JournalEventType } from "../core/models";
import type { MediaAsset } from "../services/api";
import { t } from "../core/i18n";
import {
  createJournalComposerEl,
  journalEventLabel,
  renderJournalList,
} from "../components/journal";
import { createModal, type ModalOptions } from "../components/dialogCore";
import { buildPlantNameMap } from "../core/plantNames";
import { renderMediaGallery } from "../components/mediaGallery";
import {
  fetchJournalEntriesApi,
  updateJournalEntryApi,
  deleteJournalEntryApi,
  getActiveGardenContext,
  uploadMediaApi,
  addMediaLinkApi,
  removeMediaLinkApi,
  deleteMediaAssetApi,
  listMediaApi,
  listMediaSummariesApi,
  getApiErrorMessage,
} from "../services/api";
import {
  enqueueDraft,
  captureOfflineQueueContext,
  assertOfflineQueueContext,
} from "../services/offlineQueue";
import { syncOfflineDraftsNow } from "../features/offlineFeature";
import {
  journalDraftKey, readJournalDraft, writeJournalDraft, discardJournalDraft,
  validateJournalDraft, getJournalDraftGeneration, type JournalDraft,
} from "../services/journalDraft";

let ctx: AppContext;

let journalEntries: JournalEntry[] = [];
let journalTotal = 0;
let journalOffset = 0;
let journalLoadSequence = 0;
let gardenGeneration = 0;
const openComposers = new Set<() => void>();
export interface JournalHistoryScope { plantId?: string; plotId?: string; label?: string }
export interface JournalComposerOpenOptions extends ModalOptions {
  plantIds?: string[];
  plotIds?: string[];
  prefillEventType?: JournalEventType;
  onSaved?: () => void;
}
let historyScope: JournalHistoryScope = {};
let historyReturn: (() => void) | undefined;
let journalLoadError: string | null = null;

export function openJournalHistory(scope: JournalHistoryScope, onReturn?: () => void): void {
  historyScope = { ...scope };
  historyReturn = onReturn;
  journalOffset = 0;
  journalEntries = [];
  journalTotal = 0;
  journalLoadError = null;
  resetJournalFilters();
  ctx.navigateToSubMode("journal", { triggerLoads: false });
  renderJournalView();
  void loadJournalEntries();
}
const JOURNAL_PAGE_SIZE = 50;
const MEDIA_SUMMARY_BATCH_SIZE = 80;
const journalMediaPreviewById = new Map<
  string,
  MediaAsset | null
>();
let journalMediaPreviewSeq = 0;

export function getJournalMediaPreviewById(): Map<
  string,
  MediaAsset | null
> {
  return journalMediaPreviewById;
}

export function setJournalOffset(
  offset: number,
): void {
  journalOffset = offset;
}

export function resetJournalForGardenSwitch(): void {
  gardenGeneration += 1;
  for (const close of openComposers) close();
  historyScope = {};
  historyReturn = undefined;
  journalLoadError = null;
  journalLoadSequence += 1;
  journalMediaPreviewSeq += 1;
  journalEntries = [];
  journalTotal = 0;
  journalOffset = 0;
  journalMediaPreviewById.clear();
  renderJournalView();
}

export function initJournalTab(
  appCtx: AppContext,
): void {
  ctx = appCtx;

  const addButton = document.getElementById("journal-add-btn");
  if (addButton) {
    addButton.hidden = !ctx.canWrite();
    addButton.addEventListener("click", () => {
      if (!ctx.ensureWriteAccess()) return;
      openJournalComposer();
    });
  }
  document
    .getElementById("journal-filter-type")
    ?.addEventListener("change", () => {
      journalOffset = 0;
      void loadJournalEntries();
    });
  document
    .getElementById("journal-filter-search")
    ?.addEventListener("input", () => {
      journalOffset = 0;
      void loadJournalEntries();
    });
  document
    .getElementById("journal-filter-actor")
    ?.addEventListener("input", () => {
      journalOffset = 0;
      void loadJournalEntries();
    });
  document
    .getElementById("journal-filter-from")
    ?.addEventListener("change", () => {
      journalOffset = 0;
      void loadJournalEntries();
    });
  document
    .getElementById("journal-filter-to")
    ?.addEventListener("change", () => {
      journalOffset = 0;
      void loadJournalEntries();
    });
}

export function readJournalFilters(): Record<
  string,
  string
> {
  return {
    event_type: querySelect("journal-filter-type")?.value || "",
    q: queryInput("journal-filter-search")?.value.trim() || "",
    actor: queryInput("journal-filter-actor")?.value.trim() || "",
    date_from: queryInput("journal-filter-from")?.value || "",
    date_to: queryInput("journal-filter-to")?.value || "",
  };
}

export function resetJournalFilters(): void {
  const ids = [
    "journal-filter-type",
    "journal-filter-search",
    "journal-filter-actor",
    "journal-filter-from",
    "journal-filter-to",
  ] as const;
  for (const id of ids) {
    const field = document.getElementById(id) as
      | HTMLInputElement
      | HTMLSelectElement
      | null;
    if (field) field.value = "";
  }
}

export async function loadJournalEntries(
  extra?: Record<string, string | number>,
): Promise<void> {
  if (!ctx) return;
  if (extra?.["plant_id"] || extra?.["plot_id"]) {
    historyScope = {
      ...(extra["plant_id"] ? { plantId: String(extra["plant_id"]) } : {}),
      ...(extra["plot_id"] ? { plotId: String(extra["plot_id"]) } : {}),
    };
  }
  const sequence = ++journalLoadSequence;
  try {
    const params: Record<string, string | number> = {
      limit: JOURNAL_PAGE_SIZE,
      offset: journalOffset,
    };
    for (const [key, value] of Object.entries(
      readJournalFilters(),
    )) {
      if (value) params[key] = value;
    }
    if (extra) Object.assign(params, extra);
    if (historyScope.plantId) params["plant_id"] = historyScope.plantId;
    if (historyScope.plotId) params["plot_id"] = historyScope.plotId;
    const result = await fetchJournalEntriesApi(params);
    if (sequence !== journalLoadSequence) return;
    if (result.total > 0 && result.entries.length === 0 && journalOffset > 0) {
      journalOffset = Math.max(
        0,
        Math.floor((result.total - 1) / JOURNAL_PAGE_SIZE) * JOURNAL_PAGE_SIZE,
      );
      await loadJournalEntries(extra);
      return;
    }
    journalEntries = result.entries;
    journalTotal = result.total;
    journalLoadError = null;
    renderJournalView();
  } catch (err) {
    if (sequence !== journalLoadSequence) return;
    journalLoadError = getApiErrorMessage(err);
    renderJournalView();
  }
}

export function renderJournalView(): void {
  const container = document.getElementById(
    "journal-list",
  );
  if (!container) return;
  let scopeBar = document.getElementById("journal-history-scope");
  if (!scopeBar) {
    scopeBar = document.createElement("div");
    scopeBar.id = "journal-history-scope";
    scopeBar.className = "button-row";
    container.before(scopeBar);
  }
  scopeBar.replaceChildren();
  if (historyScope.plantId || historyScope.plotId) {
    const label = document.createElement("span");
    label.textContent = historyScope.label || [
      ctx.getPlants().find((p) => p.plt_id === historyScope.plantId)?.name || historyScope.plantId,
      historyScope.plotId,
    ].filter(Boolean).join(" / ");
    const clear = document.createElement("button");
    clear.type = "button";
    clear.textContent = t("journal.clear_scope");
    clear.addEventListener("click", () => {
      historyScope = {};
      journalOffset = 0;
      renderJournalView();
      void loadJournalEntries();
    });
    scopeBar.append(label, clear);
  }
  if (historyReturn) {
    const back = document.createElement("button");
    back.type = "button";
    back.textContent = t("journal.return_to_origin");
    back.addEventListener("click", () => historyReturn?.());
    scopeBar.appendChild(back);
  }
  if (journalLoadError) {
    const error = document.createElement("p");
    error.setAttribute("role", "alert");
    error.textContent = journalLoadError;
    const retry = document.createElement("button");
    retry.type = "button";
    retry.textContent = t("common.retry");
    retry.addEventListener("click", () => void loadJournalEntries());
    container.replaceChildren(error, retry);
    document.getElementById("journal-pagination")?.replaceChildren();
    const summary = document.getElementById("journal-summary");
    if (summary) summary.textContent = "";
    return;
  }
  const summary = document.getElementById(
    "journal-summary",
  );
  if (summary) {
    summary.textContent =
      journalTotal === 0
        ? t("journal.summary_none")
        : t("journal.summary_count", {
            count: journalTotal,
          });
  }
  const plantNames = buildPlantNameMap(ctx.getPlants());
  renderJournalList(container, journalEntries, {
    mediaPreviewByEntryId: journalMediaPreviewById,
    onEdit: (entry) =>
      void openJournalComposer(entry),
    onDelete: (entry) => void deleteJournalEntry(entry),
    onEmptyAction: ctx.canWrite() ? () => openJournalComposer() : undefined,
    onPlantClick: (pltId) => {
      ctx.focusPlantsInPlantsView([pltId]);
    },
    onPlotClick: (plotId) => {
      ctx.setActiveTab("map");
      void ctx.selectPlot(plotId);
    },
    canWrite: ctx.canWrite(),
  }, plantNames);
  ctx.renderDataExportBars();
  void ensureJournalMediaPreviews(
    journalEntries.map((entry) => entry.id),
  );
  renderJournalPagination();
}

function renderJournalPagination(): void {
  const container = document.getElementById(
    "journal-pagination",
  );
  if (!container) return;
  container.replaceChildren();
  if (journalTotal <= JOURNAL_PAGE_SIZE) return;

  const page =
    Math.floor(journalOffset / JOURNAL_PAGE_SIZE) + 1;
  const totalPages = Math.ceil(
    journalTotal / JOURNAL_PAGE_SIZE,
  );

  const prev = document.createElement("button");
  prev.type = "button";
  prev.textContent = t("common.previous");
  prev.disabled = journalOffset === 0;
  prev.addEventListener("click", () => {
    journalOffset = Math.max(
      0,
      journalOffset - JOURNAL_PAGE_SIZE,
    );
    void loadJournalEntries();
  });

  const info = document.createElement("span");
  info.textContent = t("common.page_of", {
    page,
    total: totalPages,
  });

  const next = document.createElement("button");
  next.type = "button";
  next.textContent = t("common.next");
  next.disabled =
    journalOffset + JOURNAL_PAGE_SIZE >= journalTotal;
  next.addEventListener("click", () => {
    journalOffset += JOURNAL_PAGE_SIZE;
    void loadJournalEntries();
  });

  container.append(prev, info, next);
}

export async function openJournalComposer(
  editEntry?: JournalEntry,
  options: JournalComposerOpenOptions = {},
): Promise<void> {
  if (!ctx.ensureWriteAccess()) return;
  const modalParent = options.modalParent ?? (document.activeElement instanceof HTMLElement
    ? document.activeElement.closest<HTMLElement>(".modal") : null);
  const gardenId = getActiveGardenContext();
  const identity = ctx.getAuthProfile()?.username ?? "";
  const generation = gardenGeneration;
  const draftGeneration = getJournalDraftGeneration();
  const queueContext = captureOfflineQueueContext();
  const contextCurrent = () => {
    try { assertOfflineQueueContext(queueContext); } catch { return false; }
    return generation === gardenGeneration
    && draftGeneration === getJournalDraftGeneration()
    && gardenId === getActiveGardenContext()
    && identity === (ctx.getAuthProfile()?.username ?? "");
  };
  try { await ctx.ensurePlantsCacheLoaded(true); } catch (err) {
    if (contextCurrent()) ctx.showToast(getApiErrorMessage(err), "error");
    return;
  }
  if (!contextCurrent() || (modalParent && !modalParent.isConnected) || !ctx.ensureWriteAccess()) return;
  const key = journalDraftKey(identity, gardenId);
  let draft: JournalDraft | null = null;
  let storageError = false;
  if (!editEntry) {
    try { draft = readJournalDraft(key); } catch { storageError = true; }
    if (draft) {
      const choice = await chooseJournalDraft(modalParent);
      if (!contextCurrent() || (modalParent && !modalParent.isConnected) || choice === "cancel") return;
      if (choice === "discard") {
        try { discardJournalDraft(key, draft.id); draft = null; } catch {
          ctx.showToast(t("journal.draft_storage_error"), "error");
          return;
        }
      }
    }
  }
  let missingIds: string[] = [];
  if (draft) {
    const restored = validateJournalDraft(draft,
      new Set(ctx.getPlants().map((p) => p.plt_id)), new Set(ctx.getPlots().map((p) => p.plot_id)));
    draft = restored.draft;
    missingIds = restored.missingIds;
  }
  const draftId = draft?.id ?? crypto.randomUUID();
  let closed = false;
  const { dialog: modal, close: closeModal } = createModal(
    t(editEntry ? "journal.edit_entry_aria" : "journal.new_entry_aria"),
    '<div class="modal-content"></div>', {
      modalParent,
      onClose: () => {
        closed = true;
        openComposers.delete(closeModal);
        if (contextCurrent()) options.onClose?.();
      },
    });
  openComposers.add(closeModal);
  const current = () => !closed && modal.isConnected && contextCurrent();
  const content = modal.querySelector<HTMLElement>(".modal-content")!;
  const notice = document.createElement("p");
  notice.setAttribute("role", "status");
  const hints = [
    ...(missingIds.length ? [t("journal.draft_missing_links", { ids: missingIds.join(", ") })] : []),
    ...(draft?.photo_count ? [t("journal.draft_reselect_photos", { count: draft.photo_count })] : []),
  ];
  notice.textContent = storageError ? t("journal.draft_storage_error") : hints.join(" ");
  content.appendChild(notice);

  // Keep upload acknowledgements for this editor, including partially linked assets.
  const mediaProgress = new WeakMap<File, JournalMediaFileProgress>();
  const el = createJournalComposerEl({
    availablePlants: ctx
      .getPlants()
      .map((p) => ({ plt_id: p.plt_id, name: p.name })),
    availablePlots: ctx.getPlots(),
    editEntry,
    ...(options.plantIds ? { plantIds: options.plantIds } : {}),
    ...(options.plotIds ? { plotIds: options.plotIds } : {}),
    ...(options.prefillEventType ? { prefillEventType: options.prefillEventType } : {}),
    initialDraft: draft ?? undefined,
    onDraftChange: editEntry ? undefined : (fields) => {
      if (!current()) return;
      try { writeJournalDraft(key, { ...fields, id: draftId }); }
      catch { notice.textContent = t("journal.draft_storage_error"); }
    },
    onSubmit: async (data, controls) => {
      if (!current() || !ctx.ensureWriteAccess()) return;
      try {
        const { media_files, ...entryPayload } = data;
        const savedEntryId: string | null = editEntry?.id ?? null;
        if (editEntry) {
          await updateJournalEntryApi(
            editEntry.id,
            entryPayload,
          );
        } else {
          const draftPayload: Record<string, unknown> = {
            ...entryPayload,
          };
          if (media_files && media_files.length > 0) {
            draftPayload["media_files"] = media_files;
          }
          await enqueueDraft("journal", draftPayload);
          if (!contextCurrent()) return;
          try { discardJournalDraft(key, draftId); }
          catch { ctx.showToast(t("journal.draft_storage_error"), "error"); }
          ctx.showToast(
            t("offline.draft_saved"),
            "success",
          );
          void ctx.refreshOfflineIndicator();
          closeModal();
          options.onSaved?.();
          void syncOfflineDraftsNow().then(() => {
            if (contextCurrent()) void loadJournalEntries();
          }).catch((err: unknown) => {
            if (contextCurrent()) ctx.showToast(getApiErrorMessage(err), "error");
          });
          return;
        }
        if (!current()) return;
        if (savedEntryId) {
          try {
            await uploadJournalMediaFiles(
              savedEntryId,
              media_files,
              {
                plantIds: entryPayload.plant_ids,
                plotIds: entryPayload.plot_ids,
                gardenId,
                isCurrent: current,
                fileProgress: mediaProgress,
                setUploadProgress:
                  controls.setUploadProgress,
              },
            );
          } catch {
            if (!current()) return;
            ctx.showToast(
              t("media.journal_upload_partial"),
              "error",
            );
            return;
          }
        }
        if (!current()) return;
        ctx.showToast(
          t(
            editEntry
              ? "journal.entry_updated"
              : "journal.entry_added",
          ),
        );
        if (!editEntry) {
          journalOffset = 0;
        }
        closeModal();
        options.onSaved?.();
        void loadJournalEntries();
      } catch (err) {
        if (current()) ctx.showToast(getApiErrorMessage(err), "error");
      }
    },
    onCancel: closeModal,
  });

  content.appendChild(el);
  if (!editEntry) {
    const discard = document.createElement("button");
    discard.type = "button";
    discard.textContent = t("journal.discard_draft");
    discard.addEventListener("click", () => {
      if (!current()) return;
      try { discardJournalDraft(key, draftId); closeModal(); }
      catch { notice.textContent = t("journal.draft_storage_error"); }
    });
    content.appendChild(discard);
  }
  if (editEntry) {
    const mediaSection = document.createElement("section");
    mediaSection.className = "journal-existing-media";
    const heading = document.createElement("h4");
    heading.className =
      "journal-existing-media-heading";
    heading.textContent = t("media.attached_photos");
    const mediaContainer =
      document.createElement("div");
    mediaSection.append(heading, mediaContainer);
    content.appendChild(mediaSection);

    let existingAssets: MediaAsset[] = [];
    const renderExistingAssets = () => {
      if (!current()) return;
      renderMediaGallery(mediaContainer, {
        assets: existingAssets,
        emptyText: t("media.journal_empty"),
        canUpload: false,
        deleteLabel: t("common.remove"),
        onDeleteAsset: (asset) => {
          void (async () => {
            const confirmed = await ctx.confirmDialog(
              t("media.remove_confirm", {
                name:
                  asset.original_filename ||
                  t("media.untitled"),
              }),
              t("common.remove"),
            );
            if (!confirmed || !current()) return;
            try {
              await removeMediaLinkApi({
                assetId: asset.asset_id,
                targetType: "journal_entry",
                targetId: editEntry.id,
              });
              if (!current()) return;
              existingAssets = existingAssets.filter(
                (item) =>
                  item.asset_id !== asset.asset_id,
              );
              renderExistingAssets();
              await refreshJournalMediaPreviews([
                editEntry.id,
              ]);
              ctx.showToast(t("media.removed"));
            } catch (err) {
              ctx.showToast(
                getApiErrorMessage(err),
                "error",
              );
            }
          })();
        },
        onDeleteEverywhereAsset: (asset) => {
          void (async () => {
            const confirmed = await ctx.confirmDialog(
              t("media.delete_everywhere_confirm", {
                name:
                  asset.original_filename ||
                  t("media.untitled"),
                count: asset.targets.length,
              }),
              t("media.delete_everywhere"),
            );
            if (!confirmed || !current()) return;
            try {
              await deleteMediaAssetApi(asset.asset_id);
              if (!current()) return;
              existingAssets = existingAssets.filter(
                (item) =>
                  item.asset_id !== asset.asset_id,
              );
              renderExistingAssets();
              await refreshJournalMediaPreviews(
                asset.targets
                  .filter(
                    (target) =>
                      target.target_type ===
                      "journal_entry",
                  )
                  .map((target) => target.target_id),
              );
              await ctx.refreshPlantMediaPreviews(
                asset.targets
                  .filter(
                    (target) =>
                      target.target_type === "plant",
                  )
                  .map((target) =>
                    String(target.target_id),
                  ),
              );
              ctx.showToast(
                t("media.deleted_everywhere"),
              );
            } catch (err) {
              ctx.showToast(
                getApiErrorMessage(err),
                "error",
              );
            }
          })();
        },
        deleteEverywhereLabel: t(
          "media.delete_everywhere",
        ),
      });
    };
    void (async () => {
      try {
        const result = await listMediaApi({
          target_type: "journal_entry",
          target_id: String(editEntry.id),
          limit: 12,
        });
        if (!current()) return;
        existingAssets = result.items;
        renderExistingAssets();
      } catch {
        renderExistingAssets();
      }
    })();
  }
  el.querySelector<HTMLElement>("input, select, textarea")?.focus();
}

function chooseJournalDraft(modalParent?: HTMLElement | null): Promise<"resume" | "discard" | "cancel"> {
  return new Promise((resolve) => {
    const { dialog, close } = createModal(t("journal.draft_found"), '<div class="modal-content"></div>', {
      modalParent, onClose: () => { openComposers.delete(close); resolve("cancel"); },
    });
    openComposers.add(close);
    const content = dialog.querySelector(".modal-content")!;
    const text = document.createElement("p");
    text.textContent = t("journal.draft_found");
    content.appendChild(text);
    for (const choice of ["resume", "discard", "cancel"] as const) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = t(choice === "cancel" ? "common.cancel" : `journal.${choice}_draft`);
      button.addEventListener("click", () => { resolve(choice); close(); });
      content.appendChild(button);
    }
  });
}

async function deleteJournalEntry(
  entry: JournalEntry,
): Promise<void> {
  if (!ctx.ensureWriteAccess()) return;
  const context = captureOfflineQueueContext();
  const ok = await ctx.confirmDialog(
    t("journal.delete_confirm", {
      event: journalEventLabel(entry.event_type),
      date: entry.occurred_on,
    }),
    t("common.delete"),
  );
  if (!ok) return;
  try {
    assertOfflineQueueContext(context);
    await deleteJournalEntryApi(entry.id);
    assertOfflineQueueContext(context);
    journalMediaPreviewById.delete(String(entry.id));
    ctx.showToast(t("journal.entry_deleted"));
    void loadJournalEntries();
  } catch (err) {
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

export function openBatchJournalComposer(
  pltIds: string[],
  clearPlantSelection: () => void,
): void {
  void openJournalComposer(undefined, {
    plantIds: pltIds,
    onSaved: clearPlantSelection,
  });
}

interface JournalMediaFileProgress {
  operationId: string;
  assetId?: string;
  linkedPlants: Set<string>;
  linkedPlots: Set<string>;
}

export async function uploadJournalMediaFiles(
  entryId: string | number,
  files: File[],
  options: {
    plantIds?: string[];
    plotIds?: string[];
    setUploadProgress?: (pct: number | null) => void;
    gardenId?: number | null;
    isCurrent?: () => boolean;
    fileProgress?: WeakMap<File, JournalMediaFileProgress>;
  } = {},
): Promise<void> {
  if (files.length === 0) return;
  const fileProgress = options.fileProgress ?? new WeakMap<File, JournalMediaFileProgress>();
  const ensureCurrent = () => {
    if (options.isCurrent && !options.isCurrent()) throw new Error("Journal editor context changed");
  };
  for (let i = 0; i < files.length; i += 1) {
    ensureCurrent();
    const file = files[i]!;
    let progress = fileProgress.get(file);
    if (!progress) {
      progress = { operationId: crypto.randomUUID(), linkedPlants: new Set(), linkedPlots: new Set() };
      fileProgress.set(file, progress);
    }
    const uploadOptions: Parameters<typeof uploadMediaApi>[0] = {
      targetType: "journal_entry",
      targetId: entryId,
      file,
      operationId: progress.operationId,
      onProgress: (pct) => {
        if (!options.setUploadProgress) return;
        const overall = Math.round(
          ((i + pct / 100) / files.length) * 100,
        );
        options.setUploadProgress(overall);
      },
    };
    if (options.gardenId !== undefined) {
      uploadOptions.gardenId = options.gardenId;
    }
    if (!progress.assetId) {
      const uploaded = await uploadMediaApi(uploadOptions);
      progress.assetId = uploaded.asset_id;
    }
    for (const plantId of options.plantIds ?? []) {
      ensureCurrent();
      if (progress.linkedPlants.has(plantId)) continue;
      const linkOptions: Parameters<typeof addMediaLinkApi>[0] = {
        assetId: progress.assetId,
        targetType: "plant",
        targetId: plantId,
      };
      if (options.gardenId !== undefined) {
        linkOptions.gardenId = options.gardenId;
      }
      await addMediaLinkApi(linkOptions);
      progress.linkedPlants.add(plantId);
    }
    for (const plotId of options.plotIds ?? []) {
      ensureCurrent();
      if (progress.linkedPlots.has(plotId)) continue;
      const linkOptions: Parameters<typeof addMediaLinkApi>[0] = {
        assetId: progress.assetId,
        targetType: "plot",
        targetId: plotId,
      };
      if (options.gardenId !== undefined) {
        linkOptions.gardenId = options.gardenId;
      }
      await addMediaLinkApi(linkOptions);
      progress.linkedPlots.add(plotId);
    }
    ensureCurrent();
    options.setUploadProgress?.(Math.round(((i + 1) / files.length) * 100));
  }
  options.setUploadProgress?.(null);
  ensureCurrent();
  await refreshJournalMediaPreviews([entryId]);
  await ctx.refreshPlantMediaPreviews(
    options.plantIds ?? [],
  );
}

async function ensureJournalMediaPreviews(
  entryIds: Array<string | number>,
): Promise<void> {
  const requestedIds = Array.from(
    new Set(
      entryIds
        .map((entryId) => String(entryId).trim())
        .filter(Boolean),
    ),
  );
  const missingIds = requestedIds.filter(
    (entryId) => !journalMediaPreviewById.has(entryId),
  );
  if (missingIds.length === 0) return;
  const seq = ++journalMediaPreviewSeq;
  try {
    const items: Array<{
      target_id: string;
      asset: MediaAsset;
    }> = [];
    for (
      let index = 0;
      index < missingIds.length;
      index += MEDIA_SUMMARY_BATCH_SIZE
    ) {
      const result = await listMediaSummariesApi({
        targetType: "journal_entry",
        targetIds: missingIds.slice(
          index,
          index + MEDIA_SUMMARY_BATCH_SIZE,
        ),
      });
      if (seq !== journalMediaPreviewSeq) return;
      items.push(...result.items);
    }
    if (seq !== journalMediaPreviewSeq) return;
    const found = new Map(
      items.map((item) => [item.target_id, item.asset]),
    );
    for (const entryId of missingIds) {
      journalMediaPreviewById.set(
        entryId,
        found.get(entryId) ?? null,
      );
    }
    if (
      ctx.getActiveTab() === "activity" &&
      ctx.getSubMode() === "journal"
    ) {
      renderJournalView();
    }
  } catch {
    // Ignore preview failures
  }
}

export async function refreshJournalMediaPreviews(
  entryIds: Array<string | number>,
): Promise<void> {
  const requestedIds = Array.from(
    new Set(
      entryIds
        .map((entryId) => String(entryId).trim())
        .filter(Boolean),
    ),
  );
  if (requestedIds.length === 0) return;
  for (const entryId of requestedIds) {
    journalMediaPreviewById.delete(entryId);
  }
  await ensureJournalMediaPreviews(requestedIds);
}
