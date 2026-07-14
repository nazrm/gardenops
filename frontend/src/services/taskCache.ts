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
  if (entry.response.tasks.length < entry.response.total) return null;
  if (String(entry.params["view"] ?? "") !== String(params["view"] ?? "")) return null;
  if (String(entry.params["offset"] ?? "0") !== String(params["offset"] ?? "0")) return null;
  if (entry.params["task_type"] || entry.params["status"]) return null;
  const taskType = String(params["task_type"] ?? "");
  const status = String(params["status"] ?? "");
  const tasks = entry.response.tasks.filter((task) => (
    (!taskType || task.task_type === taskType)
    && (!status || task.status === status)
  ));
  return { tasks, total: tasks.length };
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
    ))
    .sort((left, right) => right.response.tasks.length - left.response.tasks.length);
  const candidate = candidates[0];
  return candidate ? [...candidate.response.tasks] : null;
}
