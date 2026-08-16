import type {
  CreateTaskBody,
  Failure,
  Page,
  PutTaskAgentBody,
  PutTaskWorkflowBody,
  StartRunResponse,
  Task,
  TaskAgentBinding,
  TaskWorkflowBinding,
  TaskSkillBinding,
} from "@friday/contracts";
import {
  validateStartRun,
  validateTask,
  validateTaskAgentBinding,
  validateTaskWorkflowBinding,
  validateTaskPage,
  validateTaskSkillBindings,
} from "@friday/contracts";
import type { FridayHttpClient } from "../http";
export interface ListTasksParams {
  limit?: number;
  cursor?: string;
}
export class TasksResource {
  constructor(private readonly http: FridayHttpClient) {}
  create(input: CreateTaskBody) {
    return this.http.requestJson<Task>({
      method: "POST",
      path: "/v1/tasks",
      body: input,
      validate: validateTask,
    });
  }
  list(params: ListTasksParams = {}) {
    return this.http.requestJson<Page<Task>>({
      method: "GET",
      path: "/v1/tasks",
      query: { limit: params.limit, cursor: params.cursor },
      validate: validateTaskPage,
    });
  }
  get(taskId: string) {
    return this.http.requestJson<Task>({
      method: "GET",
      path: `/v1/tasks/${taskId}`,
      validate: validateTask,
    });
  }
  listSkills(taskId: string) {
    return this.http.requestJson<TaskSkillBinding[]>({
      method: "GET",
      path: `/v1/tasks/${taskId}/skills`,
      validate: validateTaskSkillBindings,
    });
  }
  replaceSkills(taskId: string, skillIds: string[]) {
    return this.http.requestJson<TaskSkillBinding[]>({
      method: "PUT",
      path: `/v1/tasks/${taskId}/skills`,
      body: { skill_ids: skillIds },
      validate: validateTaskSkillBindings,
    });
  }
  getAgent(taskId: string) {
    return this.http.requestJson<TaskAgentBinding | null>({
      method: "GET",
      path: `/v1/tasks/${taskId}/agent`,
      validate: validateTaskAgentBinding,
    });
  }
  putAgent(taskId: string, input: PutTaskAgentBody) {
    return this.http.requestJson<TaskAgentBinding | null>({
      method: "PUT",
      path: `/v1/tasks/${taskId}/agent`,
      body: input,
      validate: validateTaskAgentBinding,
    });
  }
  getWorkflow(taskId: string) {
    return this.http.requestJson<TaskWorkflowBinding | null>({
      method: "GET",
      path: `/v1/tasks/${taskId}/workflow`,
      validate: validateTaskWorkflowBinding,
    });
  }
  bindWorkflow(taskId: string, workflowId: string) {
    const input: PutTaskWorkflowBody = { workflow_id: workflowId };
    return this.http.requestJson<TaskWorkflowBinding>({
      method: "PUT",
      path: `/v1/tasks/${taskId}/workflow`,
      body: input,
      validate: validateTaskWorkflowBinding,
    });
  }
  unbindWorkflow(taskId: string) {
    return this.http.requestVoid({
      method: "DELETE",
      path: `/v1/tasks/${taskId}/workflow`,
    });
  }
  startRun(taskId: string) {
    return this.http.requestJson<StartRunResponse>({
      method: "POST",
      path: `/v1/tasks/${taskId}/runs`,
      validate: validateStartRun,
    });
  }
  cancel(taskId: string) {
    return this.http.requestJson<Task>({
      method: "POST",
      path: `/v1/tasks/${taskId}/cancel`,
      validate: validateTask,
    });
  }
  complete(taskId: string) {
    return this.http.requestJson<Task>({
      method: "POST",
      path: `/v1/tasks/${taskId}/complete`,
      validate: validateTask,
    });
  }
  fail(taskId: string, failure: Failure) {
    return this.http.requestJson<Task>({
      method: "POST",
      path: `/v1/tasks/${taskId}/fail`,
      body: failure,
      validate: validateTask,
    });
  }
}
