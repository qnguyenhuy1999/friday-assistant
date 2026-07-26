import type { Failure } from "./failure";
/** Mirrors `apps/api/schemas/tasks.py`. */
export type TaskStatus =
  "pending" | "active" | "completed" | "failed" | "cancelled";
export interface Task {
  id: string;
  title: string;
  description: string;
  status: TaskStatus;
  created_at: string;
  failure: Failure | null;
}
export interface CreateTaskBody {
  title: string;
  description?: string;
}
export interface StartRunResponse {
  task_id: string;
  run_id: string;
}
