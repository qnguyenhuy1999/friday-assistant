import type { Failure } from "./failure";
/** Mirrors `apps/api/schemas/steps.py`. */
export type RunStepStatus =
  | "pending"
  | "running"
  | "waiting_for_approval"
  | "succeeded"
  | "failed"
  | "skipped"
  | "cancelled";
export interface RunStep {
  id: string;
  run_id: string;
  name: string;
  position: number;
  status: RunStepStatus;
  failure: Failure | null;
}
export interface CreateStepBody {
  name: string;
}
