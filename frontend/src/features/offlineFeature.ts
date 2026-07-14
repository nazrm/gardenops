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
  retryDraft,
  syncAllDrafts,
  isOnline,
  deserializeFiles,
} from "../services/offlineQueue";
import {
  createJournalEntryApi,
  taskActionApi,
  createIssueApi,
  createHarvestApi,
  addMediaLinkApi,
  uploadMediaApi,
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

export interface OfflineFeatureOptions {
  canManageDrafts?: () => boolean;
  onSyncComplete?: (result: SyncResult) => Promise<void> | void;
}

export function initOfflineFeature(
  helpers: OfflineMediaHelpers,
  options: OfflineFeatureOptions = {},
): void {
  mediaHelpers = helpers;
  onSyncComplete = options.onSyncComplete ?? null;
  canManageDrafts = options.canManageDrafts ?? null;
  initOfflineIndicator();
}

function restoreSerializedMedia(
  payload: Record<string, unknown>,
): SerializedFile[] {
  if (Array.isArray(payload["_serialized_media"])) {
    const serializedMedia = payload["_serialized_media"] as SerializedFile[];
    payload["media_files"] = deserializeFiles(
      serializedMedia,
    );
    delete payload["_serialized_media"];
    return serializedMedia;
  }
  return [];
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
): Parameters<typeof taskActionApi>[1] {
  const body: Parameters<typeof taskActionApi>[1] = { action };
  if (typeof payload["snooze_until"] === "string") {
    body.snooze_until = payload["snooze_until"];
  }
  if (typeof payload["reschedule_to"] === "string") {
    body.reschedule_to = payload["reschedule_to"];
  }
  if (typeof payload["notes"] === "string") {
    body.notes = payload["notes"];
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
): Promise<void> {
  if (files.length !== operationIds.length) {
    throw new Error("Offline attachment replay IDs do not match selected files");
  }
  for (let index = 0; index < files.length; index += 1) {
    const uploaded = await uploadMediaApi({
      targetType,
      targetId,
      file: files[index]!,
      gardenId,
      operationId: operationIds[index]!,
    });
    for (const linkedTarget of linkedTargets) {
      await addMediaLinkApi({
        assetId: uploaded.asset_id,
        targetType: linkedTarget.targetType,
        targetId: linkedTarget.targetId,
        gardenId,
      });
    }
  }
}

function getOfflineSyncCallbacks(): SyncCallbacks {
  return {
    journal: async (payload, draft) => {
      const gardenId = getDraftGardenId(draft);
      const serializedMedia = restoreSerializedMedia(payload);
      const mediaFiles =
        mediaHelpers.extractPendingMediaFiles(
          payload,
        );
      const created = await createJournalEntryApi(
        mediaHelpers.withoutPendingMediaFiles(
          payload,
        ) as Parameters<
          typeof createJournalEntryApi
        >[0],
        { gardenId, operationId: draft.operation_id },
      );
      if (mediaFiles.length > 0) {
        await uploadOfflineAttachments(
          "journal_entry",
          created.id,
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
          payload,
        );
      const created = await createIssueApi(
        mediaHelpers.withoutPendingMediaFiles(
          payload,
        ) as Parameters<typeof createIssueApi>[0],
        { gardenId, operationId: draft.operation_id },
      );
      await uploadOfflineAttachments(
        "issue",
        created.id,
        mediaFiles,
        attachmentOperationIds(serializedMedia, mediaFiles),
        gardenId,
      );
    },
    harvest_create: async (payload, draft) => {
      const gardenId = getDraftGardenId(draft);
      const serializedMedia = restoreSerializedMedia(payload);
      const mediaFiles =
        mediaHelpers.extractPendingMediaFiles(
          payload,
        );
      const created = await createHarvestApi(
        mediaHelpers.withoutPendingMediaFiles(
          payload,
        ) as Parameters<typeof createHarvestApi>[0],
        { gardenId, operationId: draft.operation_id },
      );
      await uploadOfflineAttachments(
        "harvest_entry",
        created.id,
        mediaFiles,
        attachmentOperationIds(serializedMedia, mediaFiles),
        gardenId,
      );
    },
    plant_media_upload: async (payload, draft) => {
      const gardenId = getDraftGardenId(draft);
      const serializedMedia = restoreSerializedMedia(payload);
      const mediaFiles =
        mediaHelpers.extractPendingMediaFiles(
          payload,
        );
      const targetId = String(
        payload["target_id"] ?? "",
      ).trim();
      if (!targetId)
        throw new Error(
          "Missing plant media target",
        );
      await mediaHelpers.uploadTargetMediaFiles(
        "plant",
        targetId,
        mediaFiles,
        {
          gardenId,
          operationIds: attachmentOperationIds(serializedMedia, mediaFiles),
        },
      );
    },
    plot_media_upload: async (payload, draft) => {
      const gardenId = getDraftGardenId(draft);
      const serializedMedia = restoreSerializedMedia(payload);
      const mediaFiles =
        mediaHelpers.extractPendingMediaFiles(
          payload,
        );
      const targetId = String(
        payload["target_id"] ?? "",
      ).trim();
      if (!targetId)
        throw new Error(
          "Missing plot media target",
        );
      await mediaHelpers.uploadTargetMediaFiles(
        "plot",
        targetId,
        mediaFiles,
        {
          gardenId,
          operationIds: attachmentOperationIds(serializedMedia, mediaFiles),
        },
      );
    },
  };
}

export async function refreshOfflineIndicator(): Promise<void> {
  const wrapper = document.getElementById(
    "offline-indicator",
  );
  if (!wrapper) return;
  const snapshot = await getOfflineQueueSnapshot();
  renderOfflineIndicator(
    wrapper,
    {
      failedDrafts: snapshot.failedDrafts,
      canManageDrafts: canManageDrafts?.() ?? true,
      online: isOnline(),
      pendingCount: snapshot.pendingCount,
      syncingCount: snapshot.syncingCount,
    },
    {
      onDiscard: (draft) => {
        void (async () => {
          const confirmed = await confirmDialog(
            t("offline.discard_confirm"),
            t("offline.discard"),
          );
          if (!confirmed) return;
          await removeDraft(draft.id);
          await refreshOfflineIndicator();
        })();
      },
      onRetry: (draft) => {
        void (async () => {
          const changed = await retryDraft(draft.id);
          if (changed && isOnline()) {
            await syncOfflineDraftsNow();
          } else {
            await refreshOfflineIndicator();
          }
        })();
      },
      onSyncNow: () => void syncOfflineDraftsNow(),
    },
  );
  updateToastRecoveryClearance(wrapper, snapshot.failedDrafts.length > 0);
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
    if (!isOnline()) {
      await refreshOfflineIndicator();
      return;
    }
    try {
      const result = await syncAllDrafts(
        getOfflineSyncCallbacks(),
      );
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
  if (!isOnline()) {
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
