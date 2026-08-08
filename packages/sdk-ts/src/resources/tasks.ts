import type {
  CreateTaskBody,
  Failure,
  Page,
  PutTaskAgentBody,
  StartRunResponse,
  Task,
  TaskAgentBinding,
  TaskSkillBinding,
} from "@friday/contracts";
import {
  validateStartRun,
  validateTask,
  validateTaskAgentBinding,
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
