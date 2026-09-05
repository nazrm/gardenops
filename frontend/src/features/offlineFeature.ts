import type {
  SyncCallbacks,
  SyncResult,
  SerializedFile,
} from "../services/offlineQueue";
import { t } from "../core/i18n";
import { showToast } from "../components/toast";
import { renderOfflineIndicator } from "../components/offlineIndicator";
import { confirmDialog } from "../components/dialogCore";
import {
  onConnectivityChange,
  onOfflineQueueChange,
  getOfflineQueueSnapshot,
  removeDraft,
  discardQuarantinedDrafts,
  retryDraft,
  syncAllDrafts,
  isOnline,
  deserializeFiles,
  assertOfflineReplayContext,
  captureOfflineQueueContext,
  assertOfflineQueueContext,
  getSavedJournalEntryId,
  saveOfflineDraftProgress,
} from "../services/offlineQueue";
import {
  createJournalEntryApi,
  taskActionApi,
  createIssueApi,
  createHarvestApi,
  addMediaLinkApi,
  uploadMediaApi,
  type RevisionedTaskActionRequest,
} from "../services/api";
import type { OfflineDraft } from "../core/models";

export interface OfflineMediaHelpers {
  extractPendingMediaFiles(
    data: Record<string, unknown>,
  ): File[];
  withoutPendingMediaFiles(
    data: Record<string, unknown>,
  ): Record<string, unknown>;
  uploadTargetMediaFiles(
    targetType: string,
    targetId: number | string,
    files: File[],
    options?: { gardenId?: number | null; operationIds?: string[] },
  ): Promise<void>;
  uploadJournalMediaFiles(
    journalEntryId: string | number,
    files: File[],
    opts: { plantIds: string[]; plotIds: string[]; gardenId?: number | null },
  ): Promise<void>;
}

let mediaHelpers: OfflineMediaHelpers;
let onSyncComplete: ((result: SyncResult) => Promise<void> | void) | null = null;
let canManageDrafts: (() => boolean) | null = null;
let syncInFlight: Promise<void> | null = null;
let onOpenSavedJournalEntry: OfflineFeatureOptions["onOpenSavedJournalEntry"];
let onJournalEntrySaved: OfflineFeatureOptions["onJournalEntrySaved"];

function canRetryOfflineDrafts(): boolean {
  return canManageDrafts?.() ?? true;
}

export interface OfflineFeatureOptions {
  canManageDrafts?: () => boolean;
  onSyncComplete?: (result: SyncResult) => Promise<void> | void;
  onOpenSavedJournalEntry?: (entryId: string | number, gardenId: number | null) => Promise<void> | void;
  onJournalEntrySaved?: (entryId: string | number, gardenId: number | null) => Promise<void> | void;
}

export function initOfflineFeature(
  helpers: OfflineMediaHelpers,
  options: OfflineFeatureOptions = {},
): void {
  mediaHelpers = helpers;
  onSyncComplete = options.onSyncComplete ?? null;
  canManageDrafts = options.canManageDrafts ?? null;
  onOpenSavedJournalEntry = options.onOpenSavedJournalEntry;
  onJournalEntrySaved = options.onJournalEntrySaved;
  initOfflineIndicator();
}

export function restoreSerializedMedia(
  payload: Record<string, unknown>,
): SerializedFile[] {
  if (Array.isArray(payload["_serialized_media"])) {
    const serializedMedia = payload["_serialized_media"] as SerializedFile[];
    return serializedMedia;
  }
  return [];
}

function replayPayload(payload: Record<string, unknown>): Record<string, unknown> {
  const { _serialized_media, _confirmed_journal_entry_id, _completed_media_ids, ...data } = payload;
  return { ...data, media_files: deserializeFiles(restoreSerializedMedia(payload)) };
}

function getDraftGardenId(draft: OfflineDraft): number | null {
  return typeof draft.garden_id === "number" && Number.isFinite(draft.garden_id)
    ? draft.garden_id
    : null;
}

function attachmentOperationIds(
  serializedMedia: SerializedFile[],
  files: File[],
): string[] {
  if (serializedMedia.length !== files.length) {
    throw new Error("Offline attachment metadata is incomplete");
  }
  const operationIds = serializedMedia.map((item) => item.operation_id);
  if (operationIds.some((operationId) => !operationId)) {
    throw new Error("Offline attachment replay ID is missing");
  }
  return operationIds;
}

function taskActionBody(
  payload: Record<string, unknown>,
  action: Parameters<typeof taskActionApi>[1]["action"],
): RevisionedTaskActionRequest {
  const expectedUpdatedAtMs = payload["expected_updated_at_ms"];
  if (
    typeof expectedUpdatedAtMs !== "number"
    || !Number.isSafeInteger(expectedUpdatedAtMs)
  ) {
    throw new Error("Offline task action is missing its expected revision");
  }
  const body: RevisionedTaskActionRequest = {
    action,
    expected_updated_at_ms: expectedUpdatedAtMs,
  };
  if (payload["confirm_outside_window"] === true) {
    body.confirm_outside_window = true;
  }
  if (typeof payload["snooze_until"] === "string") {
    body.snooze_until = payload["snooze_until"];
  }
  if (typeof payload["reschedule_to"] === "string") {
    body.reschedule_to = payload["reschedule_to"];
  }
  if (typeof payload["notes"] === "string") {
    body.notes = payload["notes"];
  }
  if (typeof payload["occurred_on"] === "string") {
    body.occurred_on = payload["occurred_on"];
  }
  if (Array.isArray(payload["observed_plot_ids"])) {
    body.observed_plot_ids = payload["observed_plot_ids"].filter(
      (plotId): plotId is string => typeof plotId === "string",
    );
  }
  if (Array.isArray(payload["completed_plant_ids"])) {
    body.completed_plant_ids = payload["completed_plant_ids"].filter(
      (plantId): plantId is string => typeof plantId === "string",
    );
  }
  if (
    payload["completion_outcome"] === "done"
    || payload["completion_outcome"] === "not_seen_blooming_this_season"
  ) {
    body.completion_outcome = payload["completion_outcome"];
  }
  return body;
}

async function uploadOfflineAttachments(
  targetType: "journal_entry" | "issue" | "harvest_entry",
  targetId: string | number,
  files: File[],
  operationIds: string[],
  gardenId: number | null,
  linkedTargets: Array<{ targetType: "plant" | "plot"; targetId: string }> = [],
  draft?: OfflineDraft,
): Promise<void> {
  if (files.length !== operationIds.length) {
    throw new Error("Offline attachment replay IDs do not match selected files");
  }
  for (let index = 0; index < files.length; index += 1) {
    if (draft) assertOfflineReplayContext(draft);
    const completed = (draft?.payload["_completed_media_ids"] as string[] | undefined) ?? [];
    if (completed.includes(operationIds[index]!)) continue;
    const uploaded = await uploadMediaApi({
      targetType,
      targetId,
      file: files[index]!,
      gardenId,
      operationId: operationIds[index]!,
    });
    for (const linkedTarget of linkedTargets) {
      if (draft) assertOfflineReplayContext(draft);
      await addMediaLinkApi({
        assetId: uploaded.asset_id,
        targetType: linkedTarget.targetType,
        targetId: linkedTarget.targetId,
        gardenId,
      });
    }
    if (draft?.type === "journal") {
      await saveOfflineDraftProgress(draft, {
        _completed_media_ids: [...completed, operationIds[index]!],
      });
    }
  }
}

export function getOfflineSyncCallbacks(): SyncCallbacks {
  return {
    journal: async (payload, draft) => {
      const gardenId = getDraftGardenId(draft);
      const serializedMedia = restoreSerializedMedia(payload);
      const mediaFiles =
        mediaHelpers.extractPendingMediaFiles(
          replayPayload(payload),
        );
      let entryId = getSavedJournalEntryId(draft);
      if (entryId === null) {
        assertOfflineReplayContext(draft);
        const created = await createJournalEntryApi(
          mediaHelpers.withoutPendingMediaFiles(
            replayPayload(payload),
          ) as Parameters<typeof createJournalEntryApi>[0],
          { gardenId, operationId: draft.operation_id },
        );
        entryId = created.id;
        await saveOfflineDraftProgress(draft, { _confirmed_journal_entry_id: entryId });
        assertOfflineReplayContext(draft);
        await onJournalEntrySaved?.(entryId, gardenId);
      }
      if (mediaFiles.length > 0) {
        await uploadOfflineAttachments(
          "journal_entry",
          entryId,
          mediaFiles,
          attachmentOperationIds(serializedMedia, mediaFiles),
          gardenId,
          [
            ...((payload["plant_ids"] as string[] | undefined) ?? []).map(
              (targetId) => ({ targetType: "plant" as const, targetId }),
            ),
            ...((payload["plot_ids"] as string[] | undefined) ?? []).map(
              (targetId) => ({ targetType: "plot" as const, targetId }),
            ),
          ],
          draft,
        );
      }
    },
    task_complete: async (payload, draft) => {
      const gardenId = getDraftGardenId(draft);
      await taskActionApi(
        String(payload["task_id"] ?? ""),
        taskActionBody(payload, "complete"),
        { gardenId, operationId: draft.operation_id },
      );
    },
    task_skip: async (payload, draft) => {
      const gardenId = getDraftGardenId(draft);
      await taskActionApi(
        String(payload["task_id"] ?? ""),
        taskActionBody(payload, "skip"),
        { gardenId, operationId: draft.operation_id },
      );
    },
    task_snooze: async (payload, draft) => {
      const gardenId = getDraftGardenId(draft);
      await taskActionApi(
        String(payload["task_id"] ?? ""),
        taskActionBody(payload, "snooze"),
        { gardenId, operationId: draft.operation_id },
      );
    },
    task_reschedule: async (payload, draft) => {
      const gardenId = getDraftGardenId(draft);
      await taskActionApi(
        String(payload["task_id"] ?? ""),
        taskActionBody(payload, "reschedule"),
        { gardenId, operationId: draft.operation_id },
      );
    },
    issue_create: async (payload, draft) => {
      const gardenId = getDraftGardenId(draft);
      const serializedMedia = restoreSerializedMedia(payload);
      const mediaFiles =
        mediaHelpers.extractPendingMediaFiles(
          replayPayload(payload),
        );
      const created = await createIssueApi(
        mediaHelpers.withoutPendingMediaFiles(
          replayPayload(payload),
        ) as Parameters<typeof createIssueApi>[0],
        { gardenId, operationId: draft.operation_id },
      );
      await uploadOfflineAttachments(
        "issue",
        created.id,
        mediaFiles,
        attachmentOperationIds(serializedMedia, mediaFiles),
        gardenId,
        [],
        draft,
      );
    },
    harvest_create: async (payload, draft) => {
      const gardenId = getDraftGardenId(draft);
      const serializedMedia = restoreSerializedMedia(payload);
      const mediaFiles =
        mediaHelpers.extractPendingMediaFiles(
          replayPayload(payload),
        );
      const created = await createHarvestApi(
        mediaHelpers.withoutPendingMediaFiles(
          replayPayload(payload),
        ) as Parameters<typeof createHarvestApi>[0],
        { gardenId, operationId: draft.operation_id },
      );
      await uploadOfflineAttachments(
        "harvest_entry",
        created.id,
        mediaFiles,
        attachmentOperationIds(serializedMedia, mediaFiles),
        gardenId,
        [],
        draft,
      );
    },
    plant_media_upload: async (payload, draft) => {
      const gardenId = getDraftGardenId(draft);
      const serializedMedia = restoreSerializedMedia(payload);
      const mediaFiles =
        mediaHelpers.extractPendingMediaFiles(
          replayPayload(payload),
        );
      const targetId = String(
        payload["target_id"] ?? "",
      ).trim();
      if (!targetId)
        throw new Error(
          "Missing plant media target",
        );
      const operationIds = attachmentOperationIds(serializedMedia, mediaFiles);
      for (let index = 0; index < mediaFiles.length; index += 1) {
        assertOfflineReplayContext(draft);
        await mediaHelpers.uploadTargetMediaFiles(
          "plant",
          targetId,
          [mediaFiles[index]!],
          {
            gardenId,
            operationIds: [operationIds[index]!],
          },
        );
      }
    },
    plot_media_upload: async (payload, draft) => {
      const gardenId = getDraftGardenId(draft);
      const serializedMedia = restoreSerializedMedia(payload);
      const mediaFiles =
        mediaHelpers.extractPendingMediaFiles(
          replayPayload(payload),
        );
      const targetId = String(
        payload["target_id"] ?? "",
      ).trim();
      if (!targetId)
        throw new Error(
          "Missing plot media target",
        );
      const operationIds = attachmentOperationIds(serializedMedia, mediaFiles);
      for (let index = 0; index < mediaFiles.length; index += 1) {
        assertOfflineReplayContext(draft);
        await mediaHelpers.uploadTargetMediaFiles(
          "plot",
          targetId,
          [mediaFiles[index]!],
          {
            gardenId,
            operationIds: [operationIds[index]!],
          },
        );
      }
    },
  };
}

export async function refreshOfflineIndicator(): Promise<void> {
  const context = captureOfflineQueueContext();
  const wrapper = document.getElementById(
    "offline-indicator",
  );
  if (!wrapper) return;
  const snapshot = await getOfflineQueueSnapshot();
  try { assertOfflineQueueContext(context); } catch {
    wrapper.replaceChildren();
    wrapper.hidden = true;
    updateToastRecoveryClearance(wrapper, false);
    return;
  }
  renderOfflineIndicator(
    wrapper,
    {
      failedDrafts: snapshot.failedDrafts,
      quarantinedCount: snapshot.quarantinedCount,
      canDiscardDrafts: true,
      canRetryDrafts: canRetryOfflineDrafts(),
      online: isOnline(),
      pendingCount: snapshot.pendingCount,
      syncingCount: snapshot.syncingCount,
    },
    {
      onDiscardQuarantined: () => {
        void (async () => {
          assertOfflineQueueContext(context);
          const confirmed = await confirmDialog(
            t("offline.discard_quarantined_confirm"),
            t("offline.discard_quarantined"),
          );
          if (!confirmed) return;
          assertOfflineQueueContext(context);
          await discardQuarantinedDrafts();
          await refreshOfflineIndicator();
        })().catch(() => showToast(t("offline.sync_failed"), "error"));
      },
      onDiscard: (draft) => {
        void (async () => {
          const context = captureOfflineQueueContext();
          const savedEntryId = getSavedJournalEntryId(draft);
          const confirmed = await confirmDialog(
            t(savedEntryId === null ? "offline.discard_confirm" : "offline.discard_attachments_confirm"),
            t(savedEntryId === null ? "offline.discard" : "offline.discard_attachments"),
          );
          if (!confirmed) return;
          assertOfflineQueueContext(context);
          await removeDraft(draft.id);
          await refreshOfflineIndicator();
        })().catch(() => showToast(t("offline.sync_failed"), "error"));
      },
      onOpenSavedRecord: onOpenSavedJournalEntry ? (draft) => {
        try { assertOfflineQueueContext(context); } catch { return; }
        const entryId = getSavedJournalEntryId(draft);
        if (entryId !== null) {
          void Promise.resolve(onOpenSavedJournalEntry?.(entryId, getDraftGardenId(draft)))
            .catch(() => showToast(t("offline.sync_failed"), "error"));
        }
      } : undefined,
      onRetry: (draft) => {
        void (async () => {
          if (!canRetryOfflineDrafts()) {
            await refreshOfflineIndicator();
            return;
          }
          const changed = await retryDraft(draft.id);
          if (changed && isOnline()) {
            await syncOfflineDraftsNow();
          } else {
            await refreshOfflineIndicator();
          }
        })().catch(() => showToast(t("offline.sync_failed"), "error"));
      },
      onSyncNow: () => void syncOfflineDraftsNow(),
    },
  );
  updateToastRecoveryClearance(wrapper, snapshot.failedDrafts.length > 0 || snapshot.quarantinedCount > 0);
}

function updateToastRecoveryClearance(
  wrapper: HTMLElement,
  hasFailures: boolean,
): void {
  const clearance = hasFailures && !wrapper.hidden
    ? Math.ceil(wrapper.getBoundingClientRect().height + 8)
    : 0;
  document.body.classList.toggle("offline-recovery-open", hasFailures);
  document.documentElement.style.setProperty(
    "--offline-recovery-offset",
    `${clearance}px`,
  );
}

export async function syncOfflineDraftsNow(): Promise<void> {
  if (syncInFlight) return syncInFlight;
  const sync = (async () => {
    const context = captureOfflineQueueContext();
    if (!isOnline() || !canRetryOfflineDrafts()) {
      await refreshOfflineIndicator();
      return;
    }
    try {
      const result = await syncAllDrafts(
        getOfflineSyncCallbacks(),
      );
      assertOfflineQueueContext(context);
      if (result.synced > 0 && result.remaining === 0) {
        showToast(
          t("offline.sync_complete"),
          "success",
        );
      } else if (result.failed > 0) {
        showToast(t("offline.sync_failed"), "error");
      }
      if (result.synced > 0) {
        await onSyncComplete?.(result);
      }
    } catch {
      showToast(t("offline.sync_failed"), "error");
    } finally {
      await refreshOfflineIndicator();
    }
  })();
  syncInFlight = sync;
  try {
    await sync;
  } finally {
    if (syncInFlight === sync) syncInFlight = null;
  }
}

async function syncPendingOfflineDrafts(): Promise<void> {
  if (!isOnline() || !canRetryOfflineDrafts()) {
    await refreshOfflineIndicator();
    return;
  }
  const snapshot = await getOfflineQueueSnapshot();
  if (snapshot.pendingCount > 0) {
    await syncOfflineDraftsNow();
    return;
  }
  await refreshOfflineIndicator();
}

function initOfflineIndicator(): void {
  let wrapper = document.getElementById(
    "offline-indicator",
  );
  if (!wrapper) {
    wrapper = document.createElement("div");
    wrapper.id = "offline-indicator";
    wrapper.className = "offline-indicator-wrapper";
    document.body.appendChild(wrapper);
  }
  void refreshOfflineIndicator();
  void syncPendingOfflineDrafts();

  onConnectivityChange((online) => {
    if (online) {
      void syncPendingOfflineDrafts();
    }
    void refreshOfflineIndicator();
  });
  window.addEventListener("focus", () => {
    void syncPendingOfflineDrafts();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      void syncPendingOfflineDrafts();
    }
  });
  onOfflineQueueChange(() => {
    void refreshOfflineIndicator();
  });
}
