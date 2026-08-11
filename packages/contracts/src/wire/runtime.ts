import type { JsonValue } from "./json-value";

/** Mirrors packages/contracts/schemas/v1/runtime/brain_action.json. */
export interface DelegateAction {
  version: 1;
  action: "delegate";
  target_agent_key: string;
  objective: string;
  input: Record<string, JsonValue>;
  expected_output_contract: string;
  reason?: string;
}

export type BrainAction =
  | {
      version: 1;
      action: "finish";
      result: { summary: string; details?: JsonValue };
    }
  | { version: 1; action: "fail"; reason: string }
  | { version: 1; action: "yield"; delay_seconds?: number; reason?: string }
  | {
      version: 1;
      action: "invoke_tool";
      tool: string;
      input: Record<string, JsonValue>;
      reason?: string;
    }
  | DelegateAction;
