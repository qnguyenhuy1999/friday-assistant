import type { CreateStepBody, Failure, Page, RunStep } from "@friday/contracts";
import { validateStep, validateStepPage } from "@friday/contracts";
import type { FridayHttpClient } from "../http";
export interface ListStepsParams {
  limit?: number;
  cursor?: string;
}
export class StepsResource {
  constructor(private readonly http: FridayHttpClient) {}
  create(runId: string, body: CreateStepBody) {
    return this.http.requestJson<RunStep>({
      method: "POST",
      path: `/v1/runs/${runId}/steps`,
      body,
      validate: validateStep,
    });
  }
  listForRun(runId: string, p: ListStepsParams = {}) {
    return this.http.requestJson<Page<RunStep>>({
      method: "GET",
      path: `/v1/runs/${runId}/steps`,
      query: { limit: p.limit, cursor: p.cursor },
      validate: validateStepPage,
    });
  }
  get(id: string) {
    return this.http.requestJson<RunStep>({
      method: "GET",
      path: `/v1/steps/${id}`,
      validate: validateStep,
    });
  }
  start(id: string) {
    return this.http.requestJson<RunStep>({
      method: "POST",
      path: `/v1/steps/${id}/start`,
      validate: validateStep,
    });
  }
  complete(id: string) {
    return this.http.requestJson<RunStep>({
      method: "POST",
      path: `/v1/steps/${id}/complete`,
      validate: validateStep,
    });
  }
  fail(id: string, failure: Failure) {
    return this.http.requestJson<RunStep>({
      method: "POST",
      path: `/v1/steps/${id}/fail`,
      body: failure,
      validate: validateStep,
    });
  }
  skip(id: string) {
    return this.http.requestJson<RunStep>({
      method: "POST",
      path: `/v1/steps/${id}/skip`,
      validate: validateStep,
    });
  }
  cancel(id: string) {
    return this.http.requestJson<RunStep>({
      method: "POST",
      path: `/v1/steps/${id}/cancel`,
      validate: validateStep,
    });
  }
}
