import type { GardenTask, TaskType } from "../core/models";
import { t } from "../core/i18n";

export interface TaskSnoozePolicy {
  defaultDate: string;
  immediate: boolean;
  label: string;
  warning?: string;
}

const ONE_WEEK_TASK_TYPES = new Set<TaskType>([
  "observe_bloom",
  "prune",
  "fertilize",
]);

function addDays(base: Date, days: number): string {
  const next = new Date(base);
  next.setDate(next.getDate() + days);
  return next.toISOString().slice(0, 10);
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
  const exceedsWindow = Boolean(
    task.window_end_on
      && (task.task_type === "prune" || task.task_type === "fertilize")
      && defaultDate > task.window_end_on,
  );

  const policy: TaskSnoozePolicy = {
    defaultDate,
    immediate: !exceedsWindow,
    label,
  };
  if (exceedsWindow) {
    policy.warning = String(t("tasks.snooze_window_warning"));
  }
  return policy;
}
