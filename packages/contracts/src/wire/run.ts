import type { Failure } from "./failure";
/** Mirrors `apps/api/schemas/runs.py`. */
export type RunStatus =
  | "queued"
  | "running"
  | "waiting_for_approval"
  | "waiting_for_delegation"
  | "succeeded"
  | "failed"
  | "cancelled";
export interface Run {
  id: string;
  task_id: string;
  status: RunStatus;
  created_at: string;
  failure: Failure | null;
  execution_id: string;
}
