/** Mirrors `apps/api/schemas/agents.py`. */
import type { JsonValue } from "./json-value";

export type AgentStatus = "active" | "disabled" | "archived";
export type AgentRevisionSourceKind = "operator" | "imported";
export interface CreateAgentBody {
  key: string;
  display_name: string;
  description?: string;
}
export interface CreateAgentRevisionBody {
  instructions: string;
  runtime_kind: string;
  runtime_config?: Record<string, JsonValue>;
  source_kind: AgentRevisionSourceKind;
}
export interface PutTaskAgentBody {
  agent_id?: string | null;
}
