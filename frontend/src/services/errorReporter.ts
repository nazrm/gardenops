/**
 * Global error reporter — captures unhandled JS errors and promise rejections,
 * batches them, and POSTs to /api/client-errors.
 */

import { redactedLocationPath } from "../core/urlSecurity";

const REPORT_URL = "/api/client-errors";
const BATCH_INTERVAL_MS = 5_000;
const MAX_QUEUE_SIZE = 20;

interface ErrorEntry {
  message: string;
  source?: string | undefined;
  lineno?: number | undefined;
  colno?: number | undefined;
  stack?: string | undefined;
  request_id?: string | undefined;
  api_path?: string | undefined;
  status_code?: number | undefined;
  handled?: boolean | undefined;
  feature_area?: string | undefined;
  url: string;
  ts: string;
  type: "error" | "unhandledrejection" | "api_error";
}

const queue: ErrorEntry[] = [];
let timerHandle: ReturnType<typeof setTimeout> | null = null;
const RECENT_KEY_TTL_MS = 60_000;
const recentKeys = new Map<string, number>();

function pruneRecentKeys(now: number): void {
  for (const [key, seenAt] of recentKeys.entries()) {
    if (now - seenAt > RECENT_KEY_TTL_MS) recentKeys.delete(key);
  }
}

function dedupeKey(entry: ErrorEntry): string {
  return [
    entry.type,
    entry.status_code ?? 0,
    entry.request_id ?? "",
    entry.api_path ?? "",
    entry.message,
  ].join("|");
}

function enqueue(entry: ErrorEntry): void {
  const now = Date.now();
  pruneRecentKeys(now);
  const key = dedupeKey(entry);
  const seenAt = recentKeys.get(key);
  if (seenAt && now - seenAt <= RECENT_KEY_TTL_MS) return;
  recentKeys.set(key, now);
  if (queue.length >= MAX_QUEUE_SIZE) return;
  queue.push(entry);
  if (!timerHandle) {
    timerHandle = setTimeout(flush, BATCH_INTERVAL_MS);
  }
}

function flush(): void {
  timerHandle = null;
  if (queue.length === 0) return;
  const batch = queue.splice(0, MAX_QUEUE_SIZE);
  for (const entry of batch) {
    try {
      navigator.sendBeacon(REPORT_URL, JSON.stringify(entry));
    } catch {
      // Silently drop — we can't report errors about reporting errors
    }
  }
}

export function initErrorReporter(): void {
  window.addEventListener("error", (e) => {
    enqueue({
      message: e.message || "Unknown error",
      source: e.filename,
      lineno: e.lineno,
      colno: e.colno,
      stack: e.error?.stack,
      url: redactedLocationPath(location.href),
      ts: new Date().toISOString(),
      type: "error",
    });
  });

  window.addEventListener("unhandledrejection", (e) => {
    const reason = e.reason;
    const message =
      reason instanceof Error ? reason.message : String(reason ?? "Unknown rejection");
    const stack = reason instanceof Error ? reason.stack : undefined;
    enqueue({
      message,
      stack,
      url: redactedLocationPath(location.href),
      ts: new Date().toISOString(),
      type: "unhandledrejection",
    });
  });
}

export function reportHandledApiError(entry: {
  message: string;
  requestId?: string | undefined;
  apiPath?: string | undefined;
  statusCode: number;
  featureArea?: string | undefined;
}): void {
  enqueue({
    message: entry.message || "Request failed",
    request_id: entry.requestId,
    api_path: entry.apiPath,
    status_code: entry.statusCode,
    handled: true,
    feature_area: entry.featureArea,
    url: redactedLocationPath(location.href),
    ts: new Date().toISOString(),
    type: "api_error",
  });
}
