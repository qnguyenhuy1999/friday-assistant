/** Mirrors `apps/api/schemas/delegations.py`. */
import type { JsonValue } from "./json-value";

export interface CreateDelegationRequestBody {
  target_agent_id: string;
  objective: string;
  input_payload?: JsonValue;
  expected_output_contract: string;
  parent_run_step_id?: string | null;
}
