import type { JsonValue } from "./json-value";

export interface CreateWorkflowBody {
  key: string;
  display_name: string;
  description?: string;
}
export interface WorkflowNodeInput {
  node_key: string;
  target_agent_id: string;
  objective: string;
  input_payload: JsonValue;
  expected_output_contract: string;
}
export interface WorkflowEdgeInput {
  from: string;
  to: string;
}
export interface CreateWorkflowRevisionBody {
  nodes: WorkflowNodeInput[];
  edges: WorkflowEdgeInput[];
  source_kind?: "operator" | "imported";
}
