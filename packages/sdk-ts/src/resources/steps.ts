import type { CreateStepBody, Failure, Page, RunStep } from "@friday/contracts";
import type { FridayHttpClient } from "../http";
export interface ListStepsParams {
  limit?: number;
  cursor?: string;
}
export class StepsResource {
  constructor(private readonly http: FridayHttpClient) {}
  create(runId: string, body: CreateStepBody) {
    return this.http.request<RunStep>({
      method: "POST",
      path: `/v1/runs/${runId}/steps`,
      body,
    });
  }
  listForRun(runId: string, p: ListStepsParams = {}) {
    return this.http.request<Page<RunStep>>({
      method: "GET",
      path: `/v1/runs/${runId}/steps`,
      query: { limit: p.limit, cursor: p.cursor },
    });
  }
  get(id: string) {
    return this.http.request<RunStep>({
      method: "GET",
      path: `/v1/steps/${id}`,
    });
  }
  start(id: string) {
    return this.http.request<RunStep>({
      method: "POST",
      path: `/v1/steps/${id}/start`,
    });
  }
  complete(id: string) {
    return this.http.request<RunStep>({
      method: "POST",
      path: `/v1/steps/${id}/complete`,
    });
  }
  fail(id: string, failure: Failure) {
    return this.http.request<RunStep>({
      method: "POST",
      path: `/v1/steps/${id}/fail`,
      body: failure,
    });
  }
  skip(id: string) {
    return this.http.request<RunStep>({
      method: "POST",
      path: `/v1/steps/${id}/skip`,
    });
  }
  cancel(id: string) {
    return this.http.request<RunStep>({
      method: "POST",
      path: `/v1/steps/${id}/cancel`,
    });
  }
}
