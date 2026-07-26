import type { Failure } from "./failure";
import type { JsonValue } from "./json-value";
/** Mirrors `apps/api/schemas/tool_invocations.py`. */
export type ToolInvocationStatus =
  "requested" | "running" | "succeeded" | "failed" | "cancelled";
export interface ToolInvocation {
  invocation_id: string;
  run_id: string;
  step_id: string | null;
  tool_name: string;
  status: ToolInvocationStatus;
  requested_at: string;
  approval_request_id: string | null;
  output: JsonValue;
  output_set: boolean;
  failure: Failure | null;
}
export interface RequestToolInvocationBody {
  tool_name: string;
  requested_input?: JsonValue;
  step_id?: string;
  approval_request_id?: string;
}
export interface MarkSucceededBody {
  output?: JsonValue;
}
export interface MarkFailedBody {
  failure: Failure;
}
