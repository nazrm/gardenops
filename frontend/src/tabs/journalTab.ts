import type { AppContext } from "../core/appContext";
import { queryInput, querySelect } from "../core/dom";
import type { JournalEntry } from "../core/models";
import type { MediaAsset } from "../services/api";
import { t } from "../core/i18n";
import {
  createJournalComposerEl,
  journalEventLabel,
  renderJournalList,
} from "../components/journal";
import { trapFocus } from "../components/dialogCore";
import { buildPlantNameMap } from "../core/plantNames";
import { renderMediaGallery } from "../components/mediaGallery";
import {
  fetchJournalEntriesApi,
  createJournalEntryApi,
  updateJournalEntryApi,
  deleteJournalEntryApi,
  batchJournalEntryApi,
  uploadMediaApi,
  addMediaLinkApi,
  removeMediaLinkApi,
  deleteMediaAssetApi,
  listMediaApi,
  listMediaSummariesApi,
  getApiErrorMessage,
} from "../services/api";
import {
  isOnline,
  enqueueDraft,
} from "../services/offlineQueue";

let ctx: AppContext;

let journalEntries: JournalEntry[] = [];
let journalTotal = 0;
let journalOffset = 0;
let journalLoadSequence = 0;
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
    renderJournalView();
  } catch (err) {
    if (sequence !== journalLoadSequence) return;
    ctx.showToast(getApiErrorMessage(err), "error");
  }
}

export function renderJournalView(): void {
  const container = document.getElementById(
    "journal-list",
  );
  if (!container) return;
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
      openJournalComposer(undefined, entry),
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

export function openJournalComposer(
  _event?: Event,
  editEntry?: JournalEntry,
): void {
  if (!ctx.ensureWriteAccess()) return;
  const modal = document.createElement("div");
  modal.className = "modal";
  modal.setAttribute("role", "dialog");
  modal.setAttribute("aria-modal", "true");
  modal.setAttribute(
    "aria-label",
    editEntry
      ? t("journal.edit_entry_aria")
      : t("journal.new_entry_aria"),
  );

  const content = document.createElement("div");
  content.className = "modal-content";

  let releaseFocusTrap: (() => void) | null = null;
  const closeModal = () => {
    releaseFocusTrap?.();
    modal.remove();
    window.removeEventListener("keydown", onEscape);
  };
  const onEscape = (e: KeyboardEvent) => {
    if (e.key === "Escape") closeModal();
  };
  window.addEventListener("keydown", onEscape);

  const el = createJournalComposerEl({
    availablePlants: ctx
      .getPlants()
      .map((p) => ({ plt_id: p.plt_id, name: p.name })),
    availablePlots: ctx
      .getPlots()
      .map((p) => p.plot_id)
      .sort(),
    editEntry,
    onSubmit: async (data, controls) => {
      try {
        const { media_files, ...entryPayload } = data;
        let savedEntryId: string | null = editEntry?.id ?? null;
        if (editEntry) {
          await updateJournalEntryApi(
            editEntry.id,
            entryPayload,
          );
        } else if (!isOnline()) {
          const draftPayload: Record<string, unknown> = {
            ...entryPayload,
          };
          if (media_files && media_files.length > 0) {
            draftPayload["media_files"] = media_files;
          }
          await enqueueDraft("journal", draftPayload);
          ctx.showToast(
            t("offline.draft_saved"),
            "success",
          );
          void ctx.refreshOfflineIndicator();
          closeModal();
          return;
        } else {
          const created =
            await createJournalEntryApi(entryPayload);
          savedEntryId = created.id;
        }
        if (savedEntryId) {
          try {
            await uploadJournalMediaFiles(
              savedEntryId,
              media_files,
              {
                plantIds: entryPayload.plant_ids,
                plotIds: entryPayload.plot_ids,
                setUploadProgress:
                  controls.setUploadProgress,
              },
            );
          } catch {
            ctx.showToast(
              t("media.journal_upload_partial"),
              "error",
            );
            closeModal();
            void loadJournalEntries();
            return;
          }
        }
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
        void loadJournalEntries();
      } catch (err) {
        ctx.showToast(getApiErrorMessage(err), "error");
      }
    },
    onCancel: closeModal,
  });

  content.appendChild(el);
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
            if (!confirmed) return;
            try {
              await removeMediaLinkApi({
                assetId: asset.asset_id,
                targetType: "journal_entry",
                targetId: editEntry.id,
              });
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
            if (!confirmed) return;
            try {
              await deleteMediaAssetApi(asset.asset_id);
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
        existingAssets = result.items;
        renderExistingAssets();
      } catch {
        renderExistingAssets();
      }
    })();
  }
  modal.appendChild(content);
  document.body.appendChild(modal);
  releaseFocusTrap = trapFocus(modal);
}

async function deleteJournalEntry(
  entry: JournalEntry,
): Promise<void> {
  if (!ctx.ensureWriteAccess()) return;
  const ok = await ctx.confirmDialog(
    t("journal.delete_confirm", {
      event: journalEventLabel(entry.event_type),
      date: entry.occurred_on,
    }),
    t("common.delete"),
  );
  if (!ok) return;
  try {
    await deleteJournalEntryApi(entry.id);
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
  const modal = document.createElement("div");
  modal.className = "modal";
  modal.setAttribute("role", "dialog");
  modal.setAttribute("aria-modal", "true");
  modal.setAttribute(
    "aria-label",
    t("plants.batch_journal_aria"),
  );

  const content = document.createElement("div");
  content.className = "modal-content";

  const closeModal = () => {
    modal.remove();
    window.removeEventListener("keydown", onEscape);
  };
  const onEscape = (e: KeyboardEvent) => {
    if (e.key === "Escape") closeModal();
  };
  window.addEventListener("keydown", onEscape);

  const el = createJournalComposerEl({
    availablePlants: ctx
      .getPlants()
      .filter((p) => pltIds.includes(p.plt_id))
      .map((p) => ({ plt_id: p.plt_id, name: p.name })),
    availablePlots: ctx
      .getPlots()
      .map((p) => p.plot_id)
      .sort(),
    plantIds: pltIds,
    onSubmit: async (data, controls) => {
      try {
        const created = await batchJournalEntryApi({
          plt_ids: pltIds,
          event_type: data.event_type,
          occurred_on: data.occurred_on,
          title: data.title,
          notes: data.notes,
          plot_ids: data.plot_ids,
        });
        try {
          await uploadJournalMediaFiles(
            created.id,
            data.media_files,
            {
              plantIds: pltIds,
              plotIds: data.plot_ids,
              setUploadProgress:
                controls.setUploadProgress,
            },
          );
        } catch {
          closeModal();
          clearPlantSelection();
          ctx.showToast(
            t("media.journal_upload_partial"),
            "error",
          );
          return;
        }
        closeModal();
        clearPlantSelection();
        ctx.showToast(
          t("plants.batch_journal_success", {
            count: pltIds.length,
          }),
        );
      } catch (err) {
        ctx.showToast(getApiErrorMessage(err), "error");
      }
    },
    onCancel: closeModal,
  });

  content.appendChild(el);
  modal.appendChild(content);
  document.body.appendChild(modal);
}

export async function uploadJournalMediaFiles(
  entryId: string | number,
  files: File[],
  options: {
    plantIds?: string[];
    plotIds?: string[];
    setUploadProgress?: (pct: number | null) => void;
    gardenId?: number | null;
  } = {},
): Promise<void> {
  if (files.length === 0) return;
  for (let i = 0; i < files.length; i += 1) {
    const file = files[i]!;
    const uploadOptions: Parameters<typeof uploadMediaApi>[0] = {
      targetType: "journal_entry",
      targetId: entryId,
      file,
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
    const uploaded = await uploadMediaApi(uploadOptions);
    for (const plantId of options.plantIds ?? []) {
      const linkOptions: Parameters<typeof addMediaLinkApi>[0] = {
        assetId: uploaded.asset_id,
        targetType: "plant",
        targetId: plantId,
      };
      if (options.gardenId !== undefined) {
        linkOptions.gardenId = options.gardenId;
      }
      await addMediaLinkApi(linkOptions);
    }
    for (const plotId of options.plotIds ?? []) {
      const linkOptions: Parameters<typeof addMediaLinkApi>[0] = {
        assetId: uploaded.asset_id,
        targetType: "plot",
        targetId: plotId,
      };
      if (options.gardenId !== undefined) {
        linkOptions.gardenId = options.gardenId;
      }
      await addMediaLinkApi(linkOptions);
    }
  }
  options.setUploadProgress?.(null);
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
