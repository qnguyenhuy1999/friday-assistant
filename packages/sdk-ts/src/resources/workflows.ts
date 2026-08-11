import type {
  CreateWorkflowBody,
  CreateWorkflowRevisionBody,
  Workflow,
  WorkflowPage,
  WorkflowRevision,
} from "@friday/contracts";
import {
  validateWorkflow,
  validateWorkflowPage,
  validateWorkflowRevision,
  validateWorkflowRevisions,
} from "@friday/contracts";
import type { FridayHttpClient } from "../http";
export interface ListWorkflowsParams {
  limit?: number;
  cursor?: string;
}
export class WorkflowsResource {
  constructor(private readonly http: FridayHttpClient) {}
  create(input: CreateWorkflowBody) {
    return this.http.requestJson<Workflow>({
      method: "POST",
      path: "/v1/workflows",
      body: input,
      validate: validateWorkflow,
    });
  }
  list(params: ListWorkflowsParams = {}) {
    return this.http.requestJson<WorkflowPage>({
      method: "GET",
      path: "/v1/workflows",
      query: { limit: params.limit, cursor: params.cursor },
      validate: validateWorkflowPage,
    });
  }
  get(workflowId: string) {
    return this.http.requestJson<Workflow>({
      method: "GET",
      path: `/v1/workflows/${workflowId}`,
      validate: validateWorkflow,
    });
  }
  createRevision(workflowId: string, input: CreateWorkflowRevisionBody) {
    return this.http.requestJson<WorkflowRevision>({
      method: "POST",
      path: `/v1/workflows/${workflowId}/revisions`,
      body: input,
      validate: validateWorkflowRevision,
    });
  }
  listRevisions(workflowId: string) {
    return this.http.requestJson<WorkflowRevision[]>({
      method: "GET",
      path: `/v1/workflows/${workflowId}/revisions`,
      validate: validateWorkflowRevisions,
    });
  }
  getRevision(workflowId: string, revisionId: string) {
    return this.http.requestJson<WorkflowRevision>({
      method: "GET",
      path: `/v1/workflows/${workflowId}/revisions/${revisionId}`,
      validate: validateWorkflowRevision,
    });
  }
  activateRevision(workflowId: string, revisionId: string) {
    return this.http.requestJson<Workflow>({
      method: "POST",
      path: `/v1/workflows/${workflowId}/revisions/${revisionId}/activate`,
      validate: validateWorkflow,
    });
  }
  disable(workflowId: string) {
    return this.http.requestJson<Workflow>({
      method: "POST",
      path: `/v1/workflows/${workflowId}/disable`,
      validate: validateWorkflow,
    });
  }
  archive(workflowId: string) {
    return this.http.requestJson<Workflow>({
      method: "POST",
      path: `/v1/workflows/${workflowId}/archive`,
      validate: validateWorkflow,
    });
  }
}
