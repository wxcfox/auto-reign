import type { TaskHistoryItemResponse } from "@/lib/types";

export const TASKS_CHANGED_EVENT = "auto-reign:tasks-changed";

export type TasksChangedDetail = {
  task?: TaskHistoryItemResponse;
};

export function notifyTasksChanged(task?: TaskHistoryItemResponse) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<TasksChangedDetail>(TASKS_CHANGED_EVENT, {
      detail: task ? { task } : {},
    }),
  );
}
