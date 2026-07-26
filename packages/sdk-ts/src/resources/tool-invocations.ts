import type {
  MarkFailedBody,
  MarkSucceededBody,
  Page,
  RequestToolInvocationBody,
  ToolInvocation,
} from "@friday/contracts";
import { validateInvocation, validateInvocationPage } from "@friday/contracts";
import type { FridayHttpClient } from "../http";
export interface ListToolInvocationsParams {
  limit?: number;
  cursor?: string;
}
export class ToolInvocationsResource {
  constructor(private readonly http: FridayHttpClient) {}
  request(runId: string, body: RequestToolInvocationBody) {
    return this.http.requestJson<ToolInvocation>({
      method: "POST",
      path: `/v1/runs/${runId}/tool-invocations`,
      body,
      validate: validateInvocation,
    });
  }
  get(id: string) {
    return this.http.requestJson<ToolInvocation>({
      method: "GET",
      path: `/v1/tool-invocations/${id}`,
      validate: validateInvocation,
    });
  }
  listForRun(runId: string, p: ListToolInvocationsParams = {}) {
    return this.http.requestJson<Page<ToolInvocation>>({
      method: "GET",
      path: `/v1/runs/${runId}/tool-invocations`,
      query: { limit: p.limit, cursor: p.cursor },
      validate: validateInvocationPage,
    });
  }
  listForStep(stepId: string, p: ListToolInvocationsParams = {}) {
    return this.http.requestJson<Page<ToolInvocation>>({
      method: "GET",
      path: `/v1/steps/${stepId}/tool-invocations`,
      query: { limit: p.limit, cursor: p.cursor },
      validate: validateInvocationPage,
    });
  }
  markRunning(id: string) {
    return this.http.requestJson<ToolInvocation>({
      method: "POST",
      path: `/v1/tool-invocations/${id}/running`,
      validate: validateInvocation,
    });
  }
  markSucceeded(id: string, body: MarkSucceededBody = {}) {
    return this.http.requestJson<ToolInvocation>({
      method: "POST",
      path: `/v1/tool-invocations/${id}/succeed`,
      body,
      validate: validateInvocation,
    });
  }
  markFailed(id: string, body: MarkFailedBody) {
    return this.http.requestJson<ToolInvocation>({
      method: "POST",
      path: `/v1/tool-invocations/${id}/fail`,
      body,
      validate: validateInvocation,
    });
  }
  cancel(id: string) {
    return this.http.requestJson<ToolInvocation>({
      method: "POST",
      path: `/v1/tool-invocations/${id}/cancel`,
      validate: validateInvocation,
    });
  }
}
