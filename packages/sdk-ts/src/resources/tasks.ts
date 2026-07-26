import type {
  CreateTaskBody,
  Failure,
  Page,
  StartRunResponse,
  Task,
} from "@friday/contracts";
import type { FridayHttpClient } from "../http";
export interface ListTasksParams {
  limit?: number;
  cursor?: string;
}
export class TasksResource {
  constructor(private readonly http: FridayHttpClient) {}
  create(input: CreateTaskBody) {
    return this.http.request<Task>({
      method: "POST",
      path: "/v1/tasks",
      body: input,
    });
  }
  list(params: ListTasksParams = {}) {
    return this.http.request<Page<Task>>({
      method: "GET",
      path: "/v1/tasks",
      query: { limit: params.limit, cursor: params.cursor },
    });
  }
  get(taskId: string) {
    return this.http.request<Task>({
      method: "GET",
      path: `/v1/tasks/${taskId}`,
    });
  }
  startRun(taskId: string) {
    return this.http.request<StartRunResponse>({
      method: "POST",
      path: `/v1/tasks/${taskId}/runs`,
    });
  }
  cancel(taskId: string) {
    return this.http.request<Task>({
      method: "POST",
      path: `/v1/tasks/${taskId}/cancel`,
    });
  }
  complete(taskId: string) {
    return this.http.request<Task>({
      method: "POST",
      path: `/v1/tasks/${taskId}/complete`,
    });
  }
  fail(taskId: string, failure: Failure) {
    return this.http.request<Task>({
      method: "POST",
      path: `/v1/tasks/${taskId}/fail`,
      body: failure,
    });
  }
}
