import type { GardenTask, TaskListResponse } from "../core/models";

interface TaskCacheEntry {
  gardenId: number;
  params: Record<string, string | number>;
  response: TaskListResponse;
}

const taskListCache = new Map<string, TaskCacheEntry>();

function normalizedParams(
  params: Record<string, string | number>,
): Array<[string, string]> {
  return Object.entries(params)
    .filter(([, value]) => value !== "")
    .map(([key, value]) => [key, String(value)] as [string, string])
    .sort(([left], [right]) => left.localeCompare(right));
}

function cacheKey(
  gardenId: number,
  params: Record<string, string | number>,
): string {
  return `${gardenId}:${JSON.stringify(normalizedParams(params))}`;
}

function cloneResponse(response: TaskListResponse): TaskListResponse {
  return { tasks: [...response.tasks], total: response.total };
}

export function cacheTaskList(
  gardenId: number,
  params: Record<string, string | number>,
  response: TaskListResponse,
): void {
  taskListCache.set(cacheKey(gardenId, params), {
    gardenId,
    params: { ...params },
    response: cloneResponse(response),
  });
}

function filterCompleteBaseSnapshot(
  entry: TaskCacheEntry,
  params: Record<string, string | number>,
): TaskListResponse | null {
  // Only a complete, first-page, unfiltered base can answer a different query.
  if (entry.response.tasks.length !== entry.response.total) return null;
  if (Number(entry.params["offset"] ?? 0) !== 0) return null;
  if (normalizedParams(entry.params).some(([key]) => !["view", "limit", "offset"].includes(key))) return null;
  if (normalizedParams(params).some(([key]) => !["view", "limit", "offset", "plot_id", "task_type", "status"].includes(key))) return null;
  if (String(entry.params["view"] ?? "") !== String(params["view"] ?? "")) return null;
  const offset = Number(params["offset"] ?? 0);
  const limit = Number(params["limit"] ?? 50);
  if (!Number.isInteger(offset) || offset < 0 || !Number.isInteger(limit) || limit < 1) return null;
  const taskType = String(params["task_type"] ?? "");
  const taskTypes = taskType.split(",").map((value) => value.trim()).filter(Boolean);
  const status = String(params["status"] ?? "");
  // Historical status changes both the temporal view's row universe and date
  // expression on the server; an actionable snapshot cannot prove its absence.
  if (["today", "week", "month", "overdue"].includes(String(params["view"] ?? ""))
    && status && !["pending", "snoozed"].includes(status)) return null;
  const plotId = String(params["plot_id"] ?? "");
  const tasks = entry.response.tasks.filter((task) => (
    (!taskTypes.length || taskTypes.includes(task.task_type))
    && (!status || task.status === status)
    && (!plotId || task.plot_ids.includes(plotId))
  ));
  // Filter first, then paginate; total is the filtered count, not page length.
  return { tasks: tasks.slice(offset, offset + limit), total: tasks.length };
}

export function getCachedTaskList(
  gardenId: number,
  params: Record<string, string | number>,
): TaskListResponse | null {
  const exact = taskListCache.get(cacheKey(gardenId, params));
  if (exact) return cloneResponse(exact.response);
  for (const entry of taskListCache.values()) {
    if (entry.gardenId !== gardenId) continue;
    const filtered = filterCompleteBaseSnapshot(entry, params);
    if (filtered) return filtered;
  }
  return null;
}

export function getCachedTodayTasks(gardenId: number): GardenTask[] | null {
  const candidates = Array.from(taskListCache.values())
    .filter((entry) => (
      entry.gardenId === gardenId
      && entry.params["view"] === "today"
      && Number(entry.params["offset"] ?? 0) === 0
      && !entry.params["task_type"]
      && !entry.params["status"]
      && !entry.params["plot_id"]
      && normalizedParams(entry.params).every(([key]) => ["view", "limit", "offset"].includes(key))
    ))
    .sort((left, right) => right.response.tasks.length - left.response.tasks.length);
  const candidate = candidates[0];
  return candidate ? [...candidate.response.tasks] : null;
}
