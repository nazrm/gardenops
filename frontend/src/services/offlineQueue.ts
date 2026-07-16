import type { OfflineDraft } from "../core/models";
import { getActiveGardenContext } from "./api";

const DB_NAME = "gardenops-offline";
const STORE_NAME = "drafts";
const DB_VERSION = 4;
const MAX_RETRIES = 5;
const MAX_TRANSIENT_ATTEMPTS_PER_SYNC = 2;
const TRANSIENT_RETRY_DELAY_MS = 300;
const QUEUE_CHANGED_EVENT = "gardenops:offline-queue-changed";
const TERMINAL_REPLAY_STATUSES = new Set([400, 403, 409, 410, 413, 422]);

export const TASK_ACTION_DRAFT_TYPES = [
  "task_complete",
  "task_skip",
  "task_snooze",
  "task_reschedule",
] as const;

export type TaskActionDraftType = typeof TASK_ACTION_DRAFT_TYPES[number];

export interface TaskActionDraftInput {
  type: TaskActionDraftType;
  payload: Record<string, unknown>;
}

export interface OfflineTaskActionState {
  action: "complete" | "skip" | "snooze" | "reschedule";
  actionLabel: string;
  createdAtMs: number;
  draftId: number;
  lastError: string;
  retryCount: number;
  status: "queued" | "syncing" | "failed";
  taskLabel: string;
  taskId: string;
  type: TaskActionDraftType;
}

export class OfflineTaskActionConflictError extends Error {
  readonly kind: "duplicate" | "conflict";
  readonly taskId: string;
  readonly existingType: TaskActionDraftType;
  readonly requestedType: TaskActionDraftType;

  constructor(options: {
    kind: "duplicate" | "conflict";
    taskId: string;
    existingType: TaskActionDraftType;
    requestedType: TaskActionDraftType;
  }) {
    super(
      options.kind === "duplicate"
        ? `An offline action is already queued for task ${options.taskId}`
        : `Task ${options.taskId} already has a different unresolved offline action`,
    );
    this.name = "OfflineTaskActionConflictError";
    this.kind = options.kind;
    this.taskId = options.taskId;
    this.existingType = options.existingType;
    this.requestedType = options.requestedType;
  }
}

export interface SerializedFile {
  name: string;
  type: string;
  buffer: ArrayBuffer;
  operation_id: string;
}

export async function serializeFiles(
  files: File[],
): Promise<SerializedFile[]> {
  return Promise.all(
    files.map(async (f) => ({
      name: f.name,
      type: f.type,
      buffer: await f.arrayBuffer(),
      operation_id: generateOperationId(),
    })),
  );
}

export function deserializeFiles(
  items: SerializedFile[],
): File[] {
  return items.map(
    (s) => new File([s.buffer], s.name, { type: s.type }),
  );
}

export interface SyncCallbacks {
  journal: (payload: Record<string, unknown>, draft: OfflineDraft) => Promise<void>;
  task_complete: (payload: Record<string, unknown>, draft: OfflineDraft) => Promise<void>;
  task_skip: (payload: Record<string, unknown>, draft: OfflineDraft) => Promise<void>;
  task_snooze: (payload: Record<string, unknown>, draft: OfflineDraft) => Promise<void>;
  task_reschedule: (payload: Record<string, unknown>, draft: OfflineDraft) => Promise<void>;
  issue_create: (payload: Record<string, unknown>, draft: OfflineDraft) => Promise<void>;
  harvest_create: (payload: Record<string, unknown>, draft: OfflineDraft) => Promise<void>;
  plant_media_upload: (payload: Record<string, unknown>, draft: OfflineDraft) => Promise<void>;
  plot_media_upload: (payload: Record<string, unknown>, draft: OfflineDraft) => Promise<void>;
}

export interface SyncResult {
  synced: number;
  syncedTypes: string[];
  failed: number;
  remaining: number;
}

export interface OfflineQueueSnapshot {
  failedDrafts: OfflineDraft[];
  pendingCount: number;
  syncingCount: number;
  taskActions: Map<string, OfflineTaskActionState>;
}

export type ReplayErrorDisposition = "terminal" | "retryable";

let db: IDBDatabase | null = null;
let activeSync: Promise<SyncResult> | null = null;

function generateOperationId(): string {
  const cryptoApi = globalThis.crypto;
  if (typeof cryptoApi?.randomUUID === "function") {
    return cryptoApi.randomUUID();
  }
  if (!cryptoApi) {
    throw new Error("Secure operation ID generation is unavailable");
  }
  const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const value = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return value.replace(
    /^(.{8})(.{4})(.{4})(.{4})(.{12})$/,
    "$1-$2-$3-$4-$5",
  );
}

function renewOperationIdentity(draft: OfflineDraft): OfflineDraft {
  const serializedMedia = draft.payload["_serialized_media"];
  return {
    ...draft,
    operation_id: generateOperationId(),
    payload: Array.isArray(serializedMedia)
      ? {
        ...draft.payload,
        _serialized_media: serializedMedia.map((item) => ({
          ...(item as SerializedFile),
          operation_id: generateOperationId(),
        })),
      }
      : draft.payload,
  };
}

export function canRetryFailedDraft(draft: OfflineDraft): boolean {
  if (draft.last_status !== 409 && draft.last_status !== 410) return true;
  if (["journal", "issue_create", "harvest_create"].includes(draft.type)) return true;
  return draft.last_status === 409
    && (draft.type === "plant_media_upload" || draft.type === "plot_media_upload");
}

function backfillDraftOperationIds(store: IDBObjectStore): void {
  const cursorRequest = store.openCursor();
  cursorRequest.onsuccess = () => {
    const cursor = cursorRequest.result;
    if (!cursor) return;
    const draft = cursor.value as Partial<OfflineDraft>;
    let operationId = draft.operation_id;
    let changed = false;
    if (typeof operationId !== "string" || !operationId) {
      operationId = generateOperationId();
      changed = true;
    }
    const payload = draft.payload;
    if (payload && Array.isArray(payload["_serialized_media"])) {
      const serializedMedia = payload["_serialized_media"] as Array<
        Partial<SerializedFile>
      >;
      const nextSerializedMedia = serializedMedia.map((item) => {
        if (typeof item.operation_id === "string" && item.operation_id) {
          return item;
        }
        changed = true;
        return { ...item, operation_id: generateOperationId() };
      });
      if (changed) {
        cursor.update({
          ...draft,
          operation_id: operationId,
          payload: { ...payload, _serialized_media: nextSerializedMedia },
        });
      }
    } else if (changed) {
      cursor.update({ ...draft, operation_id: operationId });
    }
    cursor.continue();
  };
}

function emitQueueChanged(): void {
  window.dispatchEvent(new CustomEvent(QUEUE_CHANGED_EVENT));
}

export async function initOfflineQueue(): Promise<void> {
  if (db) return;
  db = await new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const store = request.result.objectStoreNames.contains(STORE_NAME)
        ? request.transaction!.objectStore(STORE_NAME)
        : request.result.createObjectStore(STORE_NAME, {
          keyPath: "id",
          autoIncrement: true,
        });
      if (!store.indexNames.contains("status")) {
        store.createIndex("status", "status", { unique: false });
      }
      if (!store.indexNames.contains("garden_id")) {
        store.createIndex("garden_id", "garden_id", { unique: false });
      }
      backfillDraftOperationIds(store);
    };
    request.onsuccess = () => {
      const database = request.result;
      const transaction = database.transaction(STORE_NAME, "readwrite");
      const store = transaction.objectStore(STORE_NAME);
      const syncingDrafts = store.index("status").openCursor(IDBKeyRange.only("syncing"));
      let changed = false;
      syncingDrafts.onsuccess = () => {
        const cursor = syncingDrafts.result;
        if (!cursor) return;
        const draft = cursor.value as OfflineDraft;
        cursor.update({ ...draft, status: "pending" });
        changed = true;
        cursor.continue();
      };
      syncingDrafts.onerror = () => {
        reject(new Error(`IDB request failed: ${syncingDrafts.error?.message}`));
      };
      transaction.oncomplete = () => {
        if (changed) emitQueueChanged();
        resolve(database);
      };
      transaction.onerror = () => {
        reject(new Error(`IDB transaction failed: ${transaction.error?.message}`));
      };
      transaction.onabort = () => {
        reject(new Error("IDB transaction was aborted"));
      };
    };
    request.onerror = () =>
      reject(new Error(`IndexedDB open failed: ${request.error?.message}`));
  });
}

function getStore(mode: IDBTransactionMode): IDBObjectStore {
  if (!db) throw new Error("Offline queue not initialized");
  const tx = db.transaction(STORE_NAME, mode);
  return tx.objectStore(STORE_NAME);
}

function wrap<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () =>
      reject(new Error(`IDB request failed: ${request.error?.message}`));
  });
}

function isTaskActionDraftType(type: string): type is TaskActionDraftType {
  return (TASK_ACTION_DRAFT_TYPES as readonly string[]).includes(type);
}

function taskActionForDraftType(
  type: TaskActionDraftType,
): OfflineTaskActionState["action"] {
  if (type === "task_complete") return "complete";
  if (type === "task_skip") return "skip";
  if (type === "task_snooze") return "snooze";
  return "reschedule";
}

function taskIdForPayload(payload: Record<string, unknown>): string {
  return String(payload["task_id"] ?? "").trim();
}

function taskLabelForPayload(payload: Record<string, unknown>, taskId: string): string {
  const label = payload["task_label"];
  return typeof label === "string" && label.trim() ? label.trim() : taskId;
}

function actionLabelForPayload(
  payload: Record<string, unknown>,
  action: OfflineTaskActionState["action"],
): string {
  const label = payload["action_label"];
  return typeof label === "string" && label.trim() ? label.trim() : action;
}

function sameTaskActionDraft(
  existing: OfflineDraft,
  requested: Omit<OfflineDraft, "id">,
): boolean {
  return existing.type === requested.type
    && JSON.stringify(existing.payload) === JSON.stringify(requested.payload);
}

function isPendingTaskSnoozeCorrection(
  existing: OfflineDraft,
  requested: Omit<OfflineDraft, "id">,
): boolean {
  return existing.status === "pending"
    && existing.type === "task_snooze"
    && requested.type === "task_snooze"
    && existing.garden_id === requested.garden_id
    && taskIdForPayload(existing.payload) === taskIdForPayload(requested.payload)
    && typeof existing.payload["snooze_until"] === "string"
    && typeof requested.payload["snooze_until"] === "string"
    && existing.payload["snooze_until"] !== requested.payload["snooze_until"];
}

function replacePendingTaskSnoozeDraft(
  existing: OfflineDraft,
  requested: Omit<OfflineDraft, "id">,
): OfflineDraft {
  return {
    ...requested,
    id: existing.id,
    payload: {
      ...requested.payload,
      expected_updated_at_ms: existing.payload["expected_updated_at_ms"],
    },
  };
}

function createDraft(
  type: string,
  payload: Record<string, unknown>,
): Omit<OfflineDraft, "id"> {
  return {
    type,
    payload,
    operation_id: generateOperationId(),
    garden_id: getActiveGardenContext(),
    created_at_ms: Date.now(),
    status: "pending",
    retry_count: 0,
    last_error: "",
    last_status: null,
  };
}

async function serializeDraftFiles(
  payload: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const raw = payload["media_files"];
  if (!Array.isArray(raw) || raw.length === 0) return payload;
  const files = raw.filter((item): item is File => item instanceof File);
  if (files.length === 0) return payload;
  const serializedPayload = { ...payload };
  serializedPayload["_serialized_media"] = await serializeFiles(files);
  delete serializedPayload["media_files"];
  return serializedPayload;
}

export async function enqueueTaskActionBatch(
  inputs: readonly TaskActionDraftInput[],
): Promise<number[]> {
  if (inputs.length === 0) return [];
  const drafts = inputs.map(({ type, payload }) => createDraft(type, { ...payload }));
  const requestedByTask = new Map<string, Omit<OfflineDraft, "id">>();
  for (const draft of drafts) {
    const taskId = taskIdForPayload(draft.payload);
    if (!taskId) throw new Error("Offline task action is missing a task ID");
    const prior = requestedByTask.get(taskId);
    if (prior) {
      throw new OfflineTaskActionConflictError({
        kind: sameTaskActionDraft(prior as OfflineDraft, draft) ? "duplicate" : "conflict",
        taskId,
        existingType: prior.type as TaskActionDraftType,
        requestedType: draft.type as TaskActionDraftType,
      });
    }
    requestedByTask.set(taskId, draft);
  }

  if (!db) await initOfflineQueue();
  return new Promise<number[]>((resolve, reject) => {
    const transaction = db!.transaction(STORE_NAME, "readwrite");
    const store = transaction.objectStore(STORE_NAME);
    const existingRequest = store.getAll();
    const ids: number[] = [];
    const snoozeCorrections = new Map<string, OfflineDraft>();
    let failure: Error | null = null;
    let settled = false;

    const rejectOnce = (error: Error): void => {
      if (settled) return;
      settled = true;
      reject(error);
    };

    existingRequest.onerror = () => {
      failure = new Error(`IDB request failed: ${existingRequest.error?.message}`);
    };
    existingRequest.onsuccess = () => {
      const existingDrafts = (existingRequest.result as OfflineDraft[]).filter(
        (draft) => draft.status === "pending"
          || draft.status === "syncing"
          || draft.status === "failed",
      );
      for (const requested of drafts) {
        const taskId = taskIdForPayload(requested.payload);
        const existing = existingDrafts.find((candidate) => (
          candidate.garden_id === requested.garden_id
          && isTaskActionDraftType(candidate.type)
          && taskIdForPayload(candidate.payload) === taskId
        ));
        if (!existing) continue;
        if (isPendingTaskSnoozeCorrection(existing, requested)) {
          snoozeCorrections.set(
            taskId,
            replacePendingTaskSnoozeDraft(existing, requested),
          );
          continue;
        }
        failure = new OfflineTaskActionConflictError({
          kind: sameTaskActionDraft(existing, requested) ? "duplicate" : "conflict",
          taskId,
          existingType: existing.type as TaskActionDraftType,
          requestedType: requested.type as TaskActionDraftType,
        });
        transaction.abort();
        return;
      }

      for (const draft of drafts) {
        const correction = snoozeCorrections.get(taskIdForPayload(draft.payload));
        const writeRequest = correction ? store.put(correction) : store.add(draft);
        writeRequest.onsuccess = () => {
          ids.push(writeRequest.result as number);
        };
        writeRequest.onerror = () => {
          failure = new Error(`IDB request failed: ${writeRequest.error?.message}`);
        };
      }
    };
    transaction.oncomplete = () => {
      if (settled) return;
      settled = true;
      emitQueueChanged();
      resolve(ids);
    };
    transaction.onerror = () => {
      rejectOnce(failure ?? new Error(`IDB transaction failed: ${transaction.error?.message}`));
    };
    transaction.onabort = () => {
      rejectOnce(failure ?? new Error("IDB transaction was aborted"));
    };
  });
}

export async function enqueueDraft(
  type: string,
  payload: Record<string, unknown>,
): Promise<number> {
  if (isTaskActionDraftType(type)) {
    const ids = await enqueueTaskActionBatch([{ type, payload }]);
    return ids[0]!;
  }
  const draft = createDraft(type, await serializeDraftFiles(payload));
  const store = getStore("readwrite");
  const id = await wrap(store.add(draft));
  emitQueueChanged();
  return id as number;
}

export async function getAllDrafts(): Promise<OfflineDraft[]> {
  const store = getStore("readonly");
  return await wrap(store.getAll()) as OfflineDraft[];
}

export async function getPendingDrafts(): Promise<OfflineDraft[]> {
  const store = getStore("readonly");
  const index = store.index("status");
  const all = await wrap(index.getAll("pending"));
  return all as OfflineDraft[];
}

export async function getPendingCount(): Promise<number> {
  const store = getStore("readonly");
  const index = store.index("status");
  const count = await wrap(index.count("pending"));
  return count;
}

export async function getOfflineQueueSnapshot(
  gardenId?: number | null,
): Promise<OfflineQueueSnapshot> {
  const allDrafts = await getAllDrafts();
  const scopedDrafts = gardenId === undefined
    ? allDrafts
    : allDrafts.filter((draft) => draft.garden_id === gardenId);
  const failedDrafts = scopedDrafts.filter((draft) => draft.status === "failed");
  const taskActions = new Map<string, OfflineTaskActionState>();
  for (const draft of scopedDrafts) {
    if (!isTaskActionDraftType(draft.type)) continue;
    const taskId = taskIdForPayload(draft.payload);
    if (!taskId) continue;
    const action = taskActionForDraftType(draft.type);
    taskActions.set(taskId, {
      action,
      actionLabel: actionLabelForPayload(draft.payload, action),
      createdAtMs: draft.created_at_ms,
      draftId: draft.id,
      lastError: draft.last_error,
      retryCount: draft.retry_count,
      status: draft.status === "failed"
        ? "failed"
        : draft.status === "syncing"
          ? "syncing"
          : "queued",
      taskLabel: taskLabelForPayload(draft.payload, taskId),
      taskId,
      type: draft.type,
    });
  }
  return {
    failedDrafts,
    pendingCount: scopedDrafts.filter((draft) => draft.status === "pending").length,
    syncingCount: scopedDrafts.filter((draft) => draft.status === "syncing").length,
    taskActions,
  };
}

export async function getTaskActionStates(
  gardenId: number | null,
): Promise<Map<string, OfflineTaskActionState>> {
  return (await getOfflineQueueSnapshot(gardenId)).taskActions;
}

export async function removeDraft(id: number): Promise<void> {
  const store = getStore("readwrite");
  await wrap(store.delete(id));
  emitQueueChanged();
}

export async function retryDraft(id: number): Promise<boolean> {
  const store = getStore("readwrite");
  const existing = await wrap(store.get(id));
  if (!existing) return false;
  let draft = existing as OfflineDraft;
  if (draft.status !== "failed") return false;
  if (!canRetryFailedDraft(draft)) return false;
  if (draft.last_status === 409 || draft.last_status === 410) {
    draft = renewOperationIdentity(draft);
  }
  draft.status = "pending";
  draft.retry_count = 0;
  draft.last_error = "";
  draft.last_status = null;
  await wrap(store.put(draft));
  emitQueueChanged();
  return true;
}

export async function clearOfflineQueue(): Promise<void> {
  if (!db) await initOfflineQueue();
  const store = getStore("readwrite");
  await wrap(store.clear());
  emitQueueChanged();
}

export function classifyReplayError(error: unknown): ReplayErrorDisposition {
  if (typeof error !== "object" || error === null) return "retryable";
  const status = (error as { status?: unknown }).status;
  if (typeof status !== "number") return "retryable";
  return TERMINAL_REPLAY_STATUSES.has(status) ? "terminal" : "retryable";
}

export function transitionDraftAfterReplayError(
  draft: OfflineDraft,
  error: unknown,
): OfflineDraft {
  const retryCount = draft.retry_count + 1;
  const disposition = classifyReplayError(error);
  const status = typeof error === "object" && error !== null
    && typeof (error as { status?: unknown }).status === "number"
    ? (error as { status: number }).status
    : null;
  return {
    ...draft,
    retry_count: retryCount,
    last_error: error instanceof Error ? error.message : String(error),
    last_status: status,
    status: disposition === "terminal" || retryCount >= MAX_RETRIES
      ? "failed"
      : "pending",
  };
}

async function recordReplayFailure(
  id: number,
  error: unknown,
): Promise<void> {
  const store = getStore("readwrite");
  const existing = await wrap(store.get(id));
  if (!existing) return;
  const draft = transitionDraftAfterReplayError(existing as OfflineDraft, error);
  await wrap(store.put(draft));
  emitQueueChanged();
}

async function markSyncing(id: number): Promise<OfflineDraft | null> {
  const store = getStore("readwrite");
  const existing = await wrap(store.get(id));
  if (!existing) return null;
  const draft = existing as OfflineDraft;
  if (draft.status !== "pending") return null;
  draft.status = "syncing";
  await wrap(store.put(draft));
  emitQueueChanged();
  return draft;
}

function isTransientSyncError(error: unknown): boolean {
  if (typeof error !== "object" || error === null) return false;
  const status = (error as { status?: unknown }).status;
  return typeof status === "number"
    && (status === 0
      || status === 408
      || status === 425
      || status === 429
      || (status >= 500 && status <= 599));
}

function waitForTransientRetry(attempt: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, TRANSIENT_RETRY_DELAY_MS * attempt);
  });
}

async function syncDraft(
  draft: OfflineDraft,
  handler: (payload: Record<string, unknown>, draft: OfflineDraft) => Promise<void>,
): Promise<{ synced: boolean; error?: unknown }> {
  const syncingDraft = await markSyncing(draft.id);
  if (!syncingDraft) return { synced: false };

  for (let attempt = 1; attempt <= MAX_TRANSIENT_ATTEMPTS_PER_SYNC; attempt += 1) {
    try {
      await handler(syncingDraft.payload, syncingDraft);
      await removeDraft(syncingDraft.id);
      return { synced: true };
    } catch (err) {
      const transient = isTransientSyncError(err);
      if (!transient || attempt === MAX_TRANSIENT_ATTEMPTS_PER_SYNC) {
        return {
          synced: false,
          error: err,
        };
      }
      await waitForTransientRetry(attempt);
    }
  }

  return { synced: false };
}

async function runSyncAllDrafts(
  callbacks: SyncCallbacks,
): Promise<SyncResult> {
  const drafts = await getPendingDrafts();
  let synced = 0;
  const syncedTypes = new Set<string>();
  let failed = 0;

  for (const draft of drafts) {
    const handler = callbacks[draft.type as keyof SyncCallbacks];
    if (!handler) {
      await recordReplayFailure(
        draft.id,
        Object.assign(new Error(`Unknown draft type: ${draft.type}`), { status: 400 }),
      );
      failed += 1;
      continue;
    }
    const outcome = await syncDraft(draft, handler);
    if (outcome.synced) {
      synced += 1;
      syncedTypes.add(draft.type);
    } else if (outcome.error) {
      await recordReplayFailure(draft.id, outcome.error);
      failed += 1;
    }
  }

  const remaining = (await getAllDrafts()).length;
  return { synced, syncedTypes: [...syncedTypes].sort(), failed, remaining };
}

export async function syncAllDrafts(
  callbacks: SyncCallbacks,
): Promise<SyncResult> {
  if (activeSync) return activeSync;
  const sync = runSyncAllDrafts(callbacks);
  activeSync = sync;
  try {
    return await sync;
  } finally {
    if (activeSync === sync) activeSync = null;
  }
}

export function isOnline(): boolean {
  return navigator.onLine;
}

export function onConnectivityChange(
  cb: (online: boolean) => void,
): void {
  window.addEventListener("online", () => cb(true));
  window.addEventListener("offline", () => cb(false));
}

export function onOfflineQueueChange(cb: () => void): void {
  window.addEventListener(QUEUE_CHANGED_EVENT, cb);
}
