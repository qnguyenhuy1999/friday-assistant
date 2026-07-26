import {
  validateRun,
  validateRunPage,
  type Failure,
  type Page,
  type Run,
} from "@friday/contracts";
import type { FridayHttpClient } from "../http";
export interface ListRunsParams {
  limit?: number;
  cursor?: string;
}
export class RunsResource {
  constructor(private readonly http: FridayHttpClient) {}
  get(id: string) {
    return this.http.request<Run>({
      method: "GET",
      path: `/v1/runs/${id}`,
      validate: validateRun,
    });
  }
  listForTask(id: string, p: ListRunsParams = {}) {
    return this.http.request<Page<Run>>({
      method: "GET",
      path: `/v1/tasks/${id}/runs`,
      query: { limit: p.limit, cursor: p.cursor },
      validate: validateRunPage,
    });
  }
  start(id: string) {
    return this.http.request<Run>({
      method: "POST",
      path: `/v1/runs/${id}/start`,
      validate: validateRun,
    });
  }
  complete(id: string) {
    return this.http.request<Run>({
      method: "POST",
      path: `/v1/runs/${id}/complete`,
      validate: validateRun,
    });
  }
  fail(id: string, failure: Failure) {
    return this.http.request<Run>({
      method: "POST",
      path: `/v1/runs/${id}/fail`,
      body: failure,
      validate: validateRun,
    });
  }
  cancel(id: string) {
    return this.http.request<Run>({
      method: "POST",
      path: `/v1/runs/${id}/cancel`,
      validate: validateRun,
    });
  }
  retry(id: string) {
    return this.http.request<Run>({
      method: "POST",
      path: `/v1/runs/${id}/retry`,
      validate: validateRun,
    });
  }
}
