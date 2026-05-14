import type { OfflineDraft } from "../core/models";
import { getActiveGardenContext } from "./api";

const DB_NAME = "gardenops-offline";
const STORE_NAME = "drafts";
const DB_VERSION = 2;
const MAX_RETRIES = 5;
const QUEUE_CHANGED_EVENT = "gardenops:offline-queue-changed";

export interface SerializedFile {
  name: string;
  type: string;
  buffer: ArrayBuffer;
}

export async function serializeFiles(
  files: File[],
): Promise<SerializedFile[]> {
  return Promise.all(
    files.map(async (f) => ({
      name: f.name,
      type: f.type,
      buffer: await f.arrayBuffer(),
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
  failed: number;
  remaining: number;
}

let db: IDBDatabase | null = null;

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
    };
    request.onsuccess = () => resolve(request.result);
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

export async function enqueueDraft(
  type: string,
  payload: Record<string, unknown>,
): Promise<number> {
  // Serialize File objects to ArrayBuffer before IndexedDB write —
  // File objects don't survive structured clone serialization.
  const raw = payload["media_files"];
  if (Array.isArray(raw) && raw.length > 0) {
    const files = raw.filter(
      (f): f is File => f instanceof File,
    );
    if (files.length > 0) {
      payload = { ...payload };
      payload["_serialized_media"] =
        await serializeFiles(files);
      delete payload["media_files"];
    }
  }

  const draft: Omit<OfflineDraft, "id"> = {
    type,
    payload,
    garden_id: getActiveGardenContext(),
    created_at_ms: Date.now(),
    status: "pending",
    retry_count: 0,
    last_error: "",
  };
  const store = getStore("readwrite");
  const id = await wrap(store.add(draft));
  emitQueueChanged();
  return id as number;
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

export async function removeDraft(id: number): Promise<void> {
  const store = getStore("readwrite");
  await wrap(store.delete(id));
  emitQueueChanged();
}

export async function clearOfflineQueue(): Promise<void> {
  if (!db) await initOfflineQueue();
  const store = getStore("readwrite");
  await wrap(store.clear());
  emitQueueChanged();
}

export async function markFailed(
  id: number,
  error: string,
): Promise<void> {
  const store = getStore("readwrite");
  const existing = await wrap(store.get(id));
  if (!existing) return;
  const draft = existing as OfflineDraft;
  draft.retry_count += 1;
  draft.last_error = error;
  draft.status = draft.retry_count >= MAX_RETRIES ? "failed" : "pending";
  await wrap(store.put(draft));
  emitQueueChanged();
}

export async function syncAllDrafts(
  callbacks: SyncCallbacks,
): Promise<SyncResult> {
  const drafts = await getPendingDrafts();
  let synced = 0;
  let failed = 0;

  for (const draft of drafts) {
    const handler = callbacks[draft.type as keyof SyncCallbacks];
    if (!handler) {
      await markFailed(draft.id, `Unknown draft type: ${draft.type}`);
      failed += 1;
      continue;
    }
    try {
      await handler(draft.payload, draft);
      await removeDraft(draft.id);
      synced += 1;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      await markFailed(draft.id, message);
      failed += 1;
    }
  }

  const remaining = await getPendingCount();
  return { synced, failed, remaining };
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
