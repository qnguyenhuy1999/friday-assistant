import type { JsonValue } from "./json-value";

/** Mirrors `apps/api/schemas` failure fields. */
export type FailureCause =
  | "validation"
  | "tool"
  | "runtime"
  | "approval"
  | "cancelled"
  | "timeout"
  | "internal";

export interface Failure {
  code: string;
  message: string;
  retryable: boolean;
  cause: FailureCause;
  details: JsonValue;
}
