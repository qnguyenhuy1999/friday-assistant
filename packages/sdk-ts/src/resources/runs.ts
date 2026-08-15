import {
  validateRun,
  validateRunAgentResolution,
  validateRunPage,
  validateWorkflowExecutionInspection,
  validateWorkflowNodeExecutionInspection,
  validateDelegationRequest,
  validateDelegationRequests,
  type CreateDelegationRequestBody,
  type DelegationRequest,
  type Failure,
  type Page,
  type Run,
  type RunAgentResolution,
  type RunResult,
  type WorkflowExecutionInspection,
  type WorkflowNodeExecutionInspection,
  type RunSkillBinding,
  type SkillFeedback,
  validateRunResult,
  validateRunSkillBinding,
  validateSkillFeedback,
  validateSkillFeedbackItem,
} from "@friday/contracts";
import type { FridayHttpClient } from "../http";
export interface ListRunsParams {
  limit?: number;
  cursor?: string;
}
export class RunsResource {
  constructor(private readonly http: FridayHttpClient) {}
  get(id: string) {
    return this.http.requestJson<Run>({
      method: "GET",
      path: `/v1/runs/${id}`,
      validate: validateRun,
    });
  }
  listByExecution(runId: string) {
    return this.http.requestJson<Page<Run>>({
      method: "GET",
      path: `/v1/runs/${runId}/execution`,
      validate: validateRunPage,
    });
  }
  getLatestInExecution(runId: string) {
    return this.http.requestJson<Run>({
      method: "GET",
      path: `/v1/runs/${runId}/latest-in-execution`,
      validate: validateRun,
    });
  }
  getResult(id: string) {
    return this.http.requestJson<RunResult>({
      method: "GET",
      path: `/v1/runs/${id}/result`,
      validate: validateRunResult,
    });
  }
  getSkills(runId: string) {
    return this.http.requestJson<RunSkillBinding>({
      method: "GET",
      path: `/v1/runs/${runId}/skills`,
      validate: validateRunSkillBinding,
    });
  }
  addSkillFeedback(
    runId: string,
    skillId: string,
    body: {
      rating: "helpful" | "neutral" | "harmful";
      note?: string;
      created_by: string;
    },
  ) {
    return this.http.requestJson<SkillFeedback>({
      method: "POST",
      path: `/v1/runs/${runId}/skills/${skillId}/feedback`,
      body,
      validate: validateSkillFeedbackItem,
    });
  }
  listSkillFeedback(runId: string, skillId: string) {
    return this.http.requestJson<SkillFeedback[]>({
      method: "GET",
      path: `/v1/runs/${runId}/skills/${skillId}/feedback`,
      validate: validateSkillFeedback,
    });
  }
  getWorkflow(runId: string) {
    return this.http.requestJson<WorkflowExecutionInspection>({
      method: "GET",
      path: `/v1/runs/${runId}/workflow`,
      validate: validateWorkflowExecutionInspection,
    });
  }
  getWorkflowNodes(runId: string) {
    return this.http.requestJson<WorkflowNodeExecutionInspection[]>({
      method: "GET",
      path: `/v1/runs/${runId}/workflow/nodes`,
      validate: validateWorkflowNodeExecutionInspection,
    });
  }
  getAgent(runId: string) {
    return this.http.requestJson<RunAgentResolution>({
      method: "GET",
      path: `/v1/runs/${runId}/agent`,
      validate: validateRunAgentResolution,
    });
  }
  createDelegation(runId: string, input: CreateDelegationRequestBody) {
    return this.http.requestJson<DelegationRequest>({
      method: "POST",
      path: `/v1/runs/${runId}/delegations`,
      body: input,
      validate: validateDelegationRequest,
    });
  }
  listDelegations(runId: string) {
    return this.http.requestJson<DelegationRequest[]>({
      method: "GET",
      path: `/v1/runs/${runId}/delegations`,
      validate: validateDelegationRequests,
    });
  }
  listForTask(id: string, p: ListRunsParams = {}) {
    return this.http.requestJson<Page<Run>>({
      method: "GET",
      path: `/v1/tasks/${id}/runs`,
      query: { limit: p.limit, cursor: p.cursor },
      validate: validateRunPage,
    });
  }
  start(id: string) {
    return this.http.requestJson<Run>({
      method: "POST",
      path: `/v1/runs/${id}/start`,
      validate: validateRun,
    });
  }
  complete(id: string) {
    return this.http.requestJson<Run>({
      method: "POST",
      path: `/v1/runs/${id}/complete`,
      validate: validateRun,
    });
  }
  fail(id: string, failure: Failure) {
    return this.http.requestJson<Run>({
      method: "POST",
      path: `/v1/runs/${id}/fail`,
      body: failure,
      validate: validateRun,
    });
  }
  cancel(id: string) {
    return this.http.requestJson<Run>({
      method: "POST",
      path: `/v1/runs/${id}/cancel`,
      validate: validateRun,
    });
  }
  retry(id: string) {
    return this.http.requestJson<Run>({
      method: "POST",
      path: `/v1/runs/${id}/retry`,
      validate: validateRun,
    });
  }
}
