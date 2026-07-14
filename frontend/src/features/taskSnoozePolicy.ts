import type { GardenTask, TaskType } from "../core/models";
import { t } from "../core/i18n";

export interface TaskSnoozePolicy {
  defaultDate: string;
  immediate: boolean;
  label: string;
  maxDate?: string | undefined;
  manualDateMessage?: string | undefined;
  requireManualDate?: boolean | undefined;
  blockedMessage?: string | undefined;
}

export interface TaskSnoozeDateSafety {
  blocked?: boolean | undefined;
  confirmationRequired?: boolean | undefined;
  message?: string | undefined;
  confirmationLabel?: string | undefined;
}

const ONE_WEEK_TASK_TYPES = new Set<TaskType>([
  "observe_bloom",
  "prune",
  "fertilize",
]);

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const RECURRENCE_DATE_KEYS = [
  "next_recurrence_on",
  "next_recurrence_date",
  "next_due_on",
  "next_watering_on",
  "next_occurrence_on",
];
const WEATHER_ACTION_WINDOW_DATE_KEYS = [
  "action_window_end_on",
  "action_until",
  "action_valid_until",
];

function addDays(base: Date, days: number): string {
  const next = new Date(base);
  next.setDate(next.getDate() + days);
  return formatLocalDate(next);
}

function dateFromValue(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const candidate = value.slice(0, 10);
  if (!DATE_PATTERN.test(candidate)) return undefined;
  const [yearText = "", monthText = "", dayText = ""] = candidate.split("-");
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const parsed = new Date(year, month - 1, day);
  return parsed.getFullYear() === year
    && parsed.getMonth() === month - 1
    && parsed.getDate() === day
    ? candidate
    : undefined;
}

function addDaysToLocalDate(value: string, days: number): string | undefined {
  const localDate = dateFromValue(value);
  if (!localDate) return undefined;
  const [yearText = "", monthText = "", dayText = ""] = localDate.split("-");
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  return addDays(new Date(year, month - 1, day), days);
}

function metadataDate(
  metadata: Record<string, unknown>,
  keys: readonly string[],
): string | undefined {
  for (const key of keys) {
    const date = dateFromValue(metadata[key]);
    if (date) return date;
  }
  return undefined;
}

function recordValue(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function earliestDate(dates: Array<string | undefined>): string | undefined {
  return dates.reduce<string | undefined>((earliest, date) => (
    date && (!earliest || date < earliest) ? date : earliest
  ), undefined);
}

function generatedWeatherTaskDeadline(task: GardenTask): string | undefined {
  const weatherValidUntil = metadataDate(task.metadata, ["weather_valid_until"]);
  const generatedRuleSource = task.rule_source.trim().startsWith("auto:");
  const weatherMetadata = Boolean(weatherValidUntil || task.metadata["weather_alert_id"]);
  if (!generatedRuleSource && !weatherMetadata) return undefined;
  const actionWindowEnd = earliestDate([
    dateFromValue(task.window_end_on),
    metadataDate(task.metadata, WEATHER_ACTION_WINDOW_DATE_KEYS),
  ]);
  if (!weatherValidUntil && !actionWindowEnd) return undefined;
  return earliestDate([weatherValidUntil, actionWindowEnd]);
}

function weeklyWateringNextRecurrence(task: GardenTask): string | undefined {
  if (task.task_type !== "water") return undefined;
  const metadataRecurrence = recordValue(task.metadata["recurrence"]);
  const explicitRecurrence = metadataDate(task.metadata, RECURRENCE_DATE_KEYS)
    ?? (metadataRecurrence
      ? metadataDate(metadataRecurrence, RECURRENCE_DATE_KEYS)
      : undefined);
  if (explicitRecurrence) return explicitRecurrence;

  const match = task.rule_source.trim().match(/^water:.+:(\d{4}-\d{2}-\d{2})$/);
  return match ? addDaysToLocalDate(match[1]!, 7) : undefined;
}

function noFutureSnoozeMessage(task: GardenTask, fallback?: string): string | undefined {
  if (generatedWeatherTaskDeadline(task)) {
    return String(t("tasks.snooze_weather_remains_due"));
  }
  if (weeklyWateringNextRecurrence(task)) {
    return String(t("tasks.snooze_recurrence_remains_due"));
  }
  return fallback;
}

export function formatLocalDate(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function taskSnoozeMaximumDate(task: GardenTask): string | undefined {
  const weatherDeadline = generatedWeatherTaskDeadline(task);
  const nextRecurrence = weeklyWateringNextRecurrence(task);
  const lastDateBeforeRecurrence = nextRecurrence
    ? addDaysToLocalDate(nextRecurrence, -1)
    : undefined;
  return earliestDate([weatherDeadline, lastDateBeforeRecurrence]);
}

export function taskSnoozeDateSafety(
  task: GardenTask,
  snoozeUntil: string,
): TaskSnoozeDateSafety {
  const weatherDeadline = generatedWeatherTaskDeadline(task);
  if (weatherDeadline && snoozeUntil > weatherDeadline) {
    return {
      blocked: true,
      message: String(t("tasks.snooze_weather_date_limit", { date: weatherDeadline })),
    };
  }

  const nextRecurrence = weeklyWateringNextRecurrence(task);
  if (nextRecurrence && snoozeUntil >= nextRecurrence) {
    return {
      blocked: true,
      message: String(t("tasks.snooze_recurrence_date_limit", { date: nextRecurrence })),
    };
  }

  if (
    task.window_end_on
    && (task.task_type === "prune" || task.task_type === "fertilize")
    && snoozeUntil > task.window_end_on
  ) {
    return {
      confirmationRequired: true,
      message: String(t("tasks.snooze_window_warning", { date: task.window_end_on })),
      confirmationLabel: String(t("tasks.snooze_confirm_anyway")),
    };
  }

  return {};
}

export function taskSnoozePolicy(
  task: GardenTask,
  baseDate = new Date(),
): TaskSnoozePolicy {
  const isWeekDefault = ONE_WEEK_TASK_TYPES.has(task.task_type);
  const defaultDate = addDays(baseDate, isWeekDefault ? 7 : 1);
  const label = task.task_type === "observe_bloom"
    ? String(t("tasks.snooze_check_again_week"))
    : isWeekDefault
      ? String(t("tasks.snooze_one_week"))
      : String(t("tasks.action_snooze"));
  const maxDate = taskSnoozeMaximumDate(task);
  const defaultSafety = taskSnoozeDateSafety(task, defaultDate);

  const policy: TaskSnoozePolicy = {
    defaultDate,
    immediate: !defaultSafety.blocked && !defaultSafety.confirmationRequired,
    label,
    maxDate,
  };
  if (!defaultSafety.blocked) {
    return policy;
  }

  const today = formatLocalDate(baseDate);
  if (maxDate && maxDate > today) {
    policy.defaultDate = maxDate;
    policy.requireManualDate = true;
    policy.manualDateMessage = defaultSafety.message;
  } else {
    policy.blockedMessage = noFutureSnoozeMessage(task, defaultSafety.message);
  }
  return policy;
}
