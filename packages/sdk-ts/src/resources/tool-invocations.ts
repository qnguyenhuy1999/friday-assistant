import type {
  MarkFailedBody,
  MarkSucceededBody,
  Page,
  RequestToolInvocationBody,
  ToolInvocation,
} from "@friday/contracts";
import type { FridayHttpClient } from "../http";
export interface ListToolInvocationsParams {
  limit?: number;
  cursor?: string;
}
export class ToolInvocationsResource {
  constructor(private readonly http: FridayHttpClient) {}
  request(runId: string, body: RequestToolInvocationBody) {
    return this.http.request<ToolInvocation>({
      method: "POST",
      path: `/v1/runs/${runId}/tool-invocations`,
      body,
    });
  }
  get(id: string) {
    return this.http.request<ToolInvocation>({
      method: "GET",
      path: `/v1/tool-invocations/${id}`,
    });
  }
  listForRun(runId: string, p: ListToolInvocationsParams = {}) {
    return this.http.request<Page<ToolInvocation>>({
      method: "GET",
      path: `/v1/runs/${runId}/tool-invocations`,
      query: { limit: p.limit, cursor: p.cursor },
    });
  }
  listForStep(stepId: string, p: ListToolInvocationsParams = {}) {
    return this.http.request<Page<ToolInvocation>>({
      method: "GET",
      path: `/v1/steps/${stepId}/tool-invocations`,
      query: { limit: p.limit, cursor: p.cursor },
    });
  }
  markRunning(id: string) {
    return this.http.request<ToolInvocation>({
      method: "POST",
      path: `/v1/tool-invocations/${id}/running`,
    });
  }
  markSucceeded(id: string, body: MarkSucceededBody = {}) {
    return this.http.request<ToolInvocation>({
      method: "POST",
      path: `/v1/tool-invocations/${id}/succeed`,
      body,
    });
  }
  markFailed(id: string, body: MarkFailedBody) {
    return this.http.request<ToolInvocation>({
      method: "POST",
      path: `/v1/tool-invocations/${id}/fail`,
      body,
    });
  }
  cancel(id: string) {
    return this.http.request<ToolInvocation>({
      method: "POST",
      path: `/v1/tool-invocations/${id}/cancel`,
    });
  }
}
