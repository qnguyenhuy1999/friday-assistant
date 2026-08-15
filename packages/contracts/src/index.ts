export * from "./wire/json-value";
export * from "./wire/error";
export * from "./wire/pagination";
export type { CreateTaskBody } from "./wire/task";
export type { CreateStepBody } from "./wire/step";
export type {
  AgentRevisionSourceKind,
  AgentStatus,
  CreateAgentBody,
  CreateAgentRevisionBody,
  PutTaskAgentBody,
} from "./wire/agent";
export type { CreateDelegationRequestBody } from "./wire/delegation";
export type {
  CreateWorkflowBody,
  CreateWorkflowRevisionBody,
  PutTaskWorkflowBody,
  WorkflowEdgeInput,
  WorkflowNodeInput,
} from "./wire/workflow";
export type { BrainAction, DelegateAction } from "./wire/runtime";
export type {
  CreateSkillBody,
  CreateSkillRevisionBody,
  CreateEvaluationSuiteBody,
  EvaluateImprovementProposalBody,
  RequestRollbackBody,
  ResolveSkillRequestBody,
  RunEvaluationBody,
  SkillImprovementPolicyBody,
  SkillRevisionSourceKind,
  SkillStatus,
} from "./wire/skill";
export type {
  CancelApprovalBody,
  RequestApprovalBody,
  ResolveApprovalBody,
} from "./wire/approval";
export type {
  MarkFailedBody,
  MarkSucceededBody,
  RequestToolInvocationBody,
} from "./wire/tool-invocation";
export type { RecordArtifactBody } from "./wire/artifact";
export type {
  ConversationInputMode,
  SubmitConversationTurnBody,
} from "./wire/conversation";
export type {
  Agent,
  AgentPage,
  AgentRevision,
  Workflow,
  WorkflowPage,
  WorkflowRevision,
  WorkflowExecutionInspection,
  WorkflowNodeExecutionInspection,
  ApprovalCategory,
  ApprovalRequest,
  ApprovalStatus,
  Artifact,
  ArtifactKind,
  Conversation,
  ConversationTurn,
  DelegationRequest,
  Failure,
  Run,
  RunAgentResolution,
  RunResult,
  RunEvent,
  RunEventType,
  RunStatus,
  RunStep,
  RunStepStatus,
  Schedule,
  ScheduleDeliveryPolicy,
  ScheduleFire,
  Skill,
  SkillRevision,
  TaskSkillBinding,
  RunSkillAuditItem,
  RunSkillBinding,
  SkillUsageRecord,
  SkillFeedback,
  SkillEvidenceSnapshot,
  ImprovementProposal,
  EvaluationCase,
  EvaluationSuite,
  EvaluationCaseResult,
  EvaluationRun,
  CandidateEvaluation,
  SkillPromotion,
  SkillRollback,
  SkillImprovementPolicy,
  StartRunResponse,
  TaskAgentBinding,
  TaskWorkflowBinding,
  Task,
  TaskEvent,
  TaskEventType,
  TaskStatus,
  ToolInvocation,
  ToolInvocationStatus,
} from "./wire/http.generated";
export type { CandidateEvaluation as SkillCandidateEvaluation } from "./wire/http.generated";
export {
  WireFormatError,
  validateAgent,
  validateAgentPage,
  validateAgentRevision,
  validateAgentRevisions,
  validateWorkflow,
  validateWorkflowPage,
  validateWorkflowRevision,
  validateWorkflowRevisions,
  validateWorkflowExecutionInspection,
  validateWorkflowNodeExecutionInspection,
  validateTaskAgentBinding,
  validateTaskWorkflowBinding,
  validateRunAgentResolution,
  validateDelegationRequest,
  validateDelegationRequests,
  validateRunEvent,
  validateTaskEvent,
  validateRunPage,
  validateTaskPage,
  validateRun,
  validateRunResult,
  validateTask,
  validateStep,
  validateStepPage,
  validateApproval,
  validateApprovalPage,
  validateInvocation,
  validateInvocationPage,
  validateArtifact,
  validateArtifactPage,
  validateRunEventPage,
  validateTaskEventPage,
  validateStartRun,
  validateSchedule,
  validateScheduleDeliveryPolicy,
  validateScheduleFire,
  validateScheduleFirePage,
  validateSchedulePage,
  validateSkill,
  validateSkillPage,
  validateSkillRevision,
  validateSkillRevisions,
  validateTaskSkillBindings,
  validateRunSkillBinding,
  validateSkillUsage,
  validateSkillFeedback,
  validateSkillFeedbackItem,
  validateSkillEvidenceSnapshot,
  validateSkillImprovementProposal,
  validateSkillImprovementProposals,
  validateSkillEvaluationSuite,
  validateSkillEvaluationSuites,
  validateSkillEvaluationRun,
  validateSkillCandidateEvaluation,
  validateSkillPromotion,
  validateSkillRollback,
  validateSkillImprovementPolicy,
  validateSkillImprovementPolicyRun,
  validateConversation,
  validateConversationTurn,
  validateConversationTurnPage,
  type WireValidator,
} from "./wire/response-validation";

export const CONTRACTS_VERSION = "v1" as const;

export const contractsPackageMetadata = {
  name: "@friday/contracts",
  status: "active",
  version: CONTRACTS_VERSION,
} as const;

/**
 * Repo-relative path to a canonical schema file under this version's schema
 * set, e.g. schemaPath("task/task.json") -> "schemas/v1/task/task.json".
 * Generated language bindings must resolve schemas through this helper
 * rather than hardcoding the version segment, so a version bump is a
 * one-line change.
 */
export function schemaPath(relativePath: string): string {
  return `schemas/${CONTRACTS_VERSION}/${relativePath}`;
}
