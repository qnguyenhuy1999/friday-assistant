export * from "./wire/json-value";
export * from "./wire/error";
export * from "./wire/pagination";
export type { CreateTaskBody } from "./wire/task";
export type { CreateStepBody } from "./wire/step";
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
  ApprovalCategory,
  ApprovalRequest,
  ApprovalStatus,
  Artifact,
  ArtifactKind,
  Conversation,
  ConversationTurn,
  Failure,
  Run,
  RunResult,
  RunEvent,
  RunEventType,
  RunStatus,
  RunStep,
  RunStepStatus,
  Schedule,
  ScheduleFire,
  StartRunResponse,
  Task,
  TaskEvent,
  TaskEventType,
  TaskStatus,
  ToolInvocation,
  ToolInvocationStatus,
} from "./wire/http.generated";
export {
  WireFormatError,
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
  validateScheduleFire,
  validateScheduleFirePage,
  validateSchedulePage,
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
