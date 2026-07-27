import {
  validateRun,
  validateRunPage,
  type Failure,
  type Page,
  type Run,
  type RunResult,
  validateRunResult,
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
