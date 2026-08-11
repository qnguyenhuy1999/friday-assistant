import type { JsonValue } from "./json-value";
/** Mirrors `apps/api/schemas/events.py` and SSE event names. */
export type RunEventType =
  | "run_created"
  | "run_started"
  | "run_waiting_for_approval"
  | "run_waiting_for_delegation"
  | "run_resumed"
  | "run_succeeded"
  | "run_failed"
  | "run_cancelled"
  | "step_created"
  | "step_started"
  | "step_succeeded"
  | "step_failed"
  | "step_skipped"
  | "step_cancelled"
  | "approval_requested"
  | "approval_resolved"
  | "tool_invocation_requested"
  | "tool_invocation_started"
  | "tool_invocation_succeeded"
  | "tool_invocation_failed"
  | "tool_invocation_cancelled"
  | "artifact_created"
  | "agent_finished"
  | "delegation_dispatched"
  | "delegation_succeeded"
  | "delegation_failed"
  | "delegation_cancelled"
  | "memory_context_attached"
  | "memory_retrieval_degraded"
  | "memory_write_requested"
  | "memory_write_committed"
  | "memory_write_conflicted"
  | "memory_index_marked_stale";
export interface RunEvent {
  event_id: string;
  run_id: string;
  step_id: string | null;
  type: RunEventType;
  sequence: number;
  occurred_at: string;
  payload: JsonValue;
}
export type TaskEventType = "task_completed" | "task_failed" | "task_cancelled";
export interface TaskEvent {
  event_id: string;
  task_id: string;
  type: TaskEventType;
  sequence: number;
  occurred_at: string;
  payload: JsonValue;
}
