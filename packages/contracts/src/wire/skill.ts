/** Mirrors `apps/api/schemas/skills.py`. */
import type { JsonValue } from "./json-value";

export type SkillStatus = "active" | "disabled" | "archived";
export type SkillRevisionSourceKind = "operator" | "imported";
export interface CreateSkillBody {
  key: string;
  display_name: string;
  description?: string;
}
export interface CreateSkillRevisionBody {
  instructions: string;
  source_kind: SkillRevisionSourceKind;
}
export type EvaluationGradingKind =
  | "exact_match"
  | "contains_all"
  | "contains_none"
  | "json_schema"
  | "required_keys"
  | "tool_proposal_shape";
export interface EvaluationCaseBody {
  input: string;
  expected_properties: Record<string, JsonValue>;
  grading_kind: EvaluationGradingKind;
}
export interface CreateEvaluationSuiteBody {
  name: string;
  description?: string;
  cases: EvaluationCaseBody[];
}
export interface RunEvaluationBody {
  revision_id?: string;
  proposal_id?: string;
  outputs: Record<string, string>;
}
export interface EvaluateImprovementProposalBody {
  baseline_evaluation_run_id: string;
  candidate_outputs: Record<string, string>;
  comparison_policy_version?: string;
}
export interface RequestRollbackBody {
  target_revision_id: string;
  reason: string;
}
export interface ResolveSkillRequestBody {
  resolver: string;
}
export interface SkillImprovementPolicyBody {
  enabled?: boolean;
  minimum_usage_records?: number;
  minimum_failures?: number;
  minimum_harmful_feedback?: number;
  evaluation_suite_id: string;
  cooldown_seconds?: number;
  max_open_proposals?: 1;
  evidence_window_size?: number;
  generator_version: string;
  comparison_policy_version: string;
}
