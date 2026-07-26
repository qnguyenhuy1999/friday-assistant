/** Mirrors `apps/api/errors.py`'s error envelope. */
export const KNOWN_API_ERROR_TYPES = [
  "task_not_found",
  "run_not_found",
  "run_step_not_found",
  "approval_not_found",
  "tool_invocation_not_found",
  "artifact_not_found",
  "entity_conflict",
  "concurrency_conflict",
  "transaction_failure",
  "internal_error",
  "validation_error",
] as const;
export type KnownApiErrorType = (typeof KNOWN_API_ERROR_TYPES)[number];
export function isKnownApiErrorType(value: string): value is KnownApiErrorType {
  return (KNOWN_API_ERROR_TYPES as readonly string[]).includes(value);
}
export interface ApiErrorDetail {
  type: string;
  message: string;
  details: Record<string, string>;
}
export interface ApiErrorBody {
  error: ApiErrorDetail;
}
