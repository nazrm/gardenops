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
    options?: { gardenId?: number | null },
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
): void {
  if (Array.isArray(payload["_serialized_media"])) {
    payload["media_files"] = deserializeFiles(
      payload[
        "_serialized_media"
      ] as SerializedFile[],
    );
    delete payload["_serialized_media"];
  }
}

function getDraftGardenId(draft: OfflineDraft): number | null {
  return typeof draft.garden_id === "number" && Number.isFinite(draft.garden_id)
    ? draft.garden_id
    : null;
}

function getOfflineSyncCallbacks(): SyncCallbacks {
  return {
    journal: async (payload, draft) => {
      const gardenId = getDraftGardenId(draft);
      restoreSerializedMedia(payload);
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
        { gardenId },
      );
      if (mediaFiles.length > 0) {
        await mediaHelpers.uploadJournalMediaFiles(
          created.id,
          mediaFiles,
          {
            plantIds:
              (payload["plant_ids"] as
                | string[]
                | undefined) ?? [],
            plotIds:
              (payload["plot_ids"] as
                | string[]
                | undefined) ?? [],
            gardenId,
          },
        );
      }
    },
    task_complete: async (payload, draft) => {
      const gardenId = getDraftGardenId(draft);
      await taskActionApi(
        String(payload["task_id"] ?? ""),
        { action: "complete" },
        { gardenId },
      );
    },
    task_skip: async (payload, draft) => {
      const gardenId = getDraftGardenId(draft);
      await taskActionApi(
        String(payload["task_id"] ?? ""),
        { action: "skip" },
        { gardenId },
      );
    },
    task_snooze: async (payload, draft) => {
      const gardenId = getDraftGardenId(draft);
      await taskActionApi(
        String(payload["task_id"] ?? ""),
        {
          action: "snooze",
          snooze_until: payload[
            "snooze_until"
          ] as string,
        },
        { gardenId },
      );
    },
    task_reschedule: async (payload, draft) => {
      const gardenId = getDraftGardenId(draft);
      await taskActionApi(
        String(payload["task_id"] ?? ""),
        {
          action: "reschedule",
          reschedule_to: payload[
            "reschedule_to"
          ] as string,
        },
        { gardenId },
      );
    },
    issue_create: async (payload, draft) => {
      const gardenId = getDraftGardenId(draft);
      restoreSerializedMedia(payload);
      const mediaFiles =
        mediaHelpers.extractPendingMediaFiles(
          payload,
        );
      const created = await createIssueApi(
        mediaHelpers.withoutPendingMediaFiles(
          payload,
        ) as Parameters<typeof createIssueApi>[0],
        { gardenId },
      );
      await mediaHelpers.uploadTargetMediaFiles(
        "issue",
        created.id,
        mediaFiles,
        { gardenId },
      );
    },
    harvest_create: async (payload, draft) => {
      const gardenId = getDraftGardenId(draft);
      restoreSerializedMedia(payload);
      const mediaFiles =
        mediaHelpers.extractPendingMediaFiles(
          payload,
        );
      const created = await createHarvestApi(
        mediaHelpers.withoutPendingMediaFiles(
          payload,
        ) as Parameters<typeof createHarvestApi>[0],
        { gardenId },
      );
      await mediaHelpers.uploadTargetMediaFiles(
        "harvest_entry",
        created.id,
        mediaFiles,
        { gardenId },
      );
    },
    plant_media_upload: async (payload, draft) => {
      const gardenId = getDraftGardenId(draft);
      restoreSerializedMedia(payload);
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
        { gardenId },
      );
    },
    plot_media_upload: async (payload, draft) => {
      const gardenId = getDraftGardenId(draft);
      restoreSerializedMedia(payload);
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
        { gardenId },
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
