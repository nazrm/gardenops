import type {
  SyncCallbacks,
  SerializedFile,
} from "../services/offlineQueue";
import { t } from "../core/i18n";
import { showToast } from "../components/toast";
import { renderOfflineIndicator } from "../components/offlineIndicator";
import {
  onConnectivityChange,
  onOfflineQueueChange,
  getPendingCount,
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

export function initOfflineFeature(
  helpers: OfflineMediaHelpers,
): void {
  mediaHelpers = helpers;
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
  const count = await getPendingCount();
  renderOfflineIndicator(
    wrapper,
    count,
    isOnline(),
    {
      onSyncNow: () => void triggerOfflineSync(),
    },
  );
}

async function triggerOfflineSync(): Promise<void> {
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

  onConnectivityChange((online) => {
    if (online) {
      void triggerOfflineSync();
    }
    void refreshOfflineIndicator();
  });
  onOfflineQueueChange(() => {
    void refreshOfflineIndicator();
  });
}
