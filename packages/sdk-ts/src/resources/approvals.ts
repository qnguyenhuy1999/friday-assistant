import type {
  ApprovalRequest,
  CancelApprovalBody,
  Page,
  RequestApprovalBody,
  ResolveApprovalBody,
} from "@friday/contracts";
import type { FridayHttpClient } from "../http";
export interface ListApprovalsParams {
  limit?: number;
  cursor?: string;
}
/** Literal forwards only: approval decisions remain caller-owned. */
export class ApprovalsResource {
  constructor(private readonly http: FridayHttpClient) {}
  request(runId: string, body: RequestApprovalBody) {
    return this.http.request<ApprovalRequest>({
      method: "POST",
      path: `/v1/runs/${runId}/approvals`,
      body,
    });
  }
  get(id: string) {
    return this.http.request<ApprovalRequest>({
      method: "GET",
      path: `/v1/approvals/${id}`,
    });
  }
  listForRun(runId: string, p: ListApprovalsParams = {}) {
    return this.http.request<Page<ApprovalRequest>>({
      method: "GET",
      path: `/v1/runs/${runId}/approvals`,
      query: { limit: p.limit, cursor: p.cursor },
    });
  }
  approve(id: string, body: ResolveApprovalBody) {
    return this.http.request<ApprovalRequest>({
      method: "POST",
      path: `/v1/approvals/${id}/approve`,
      body,
    });
  }
  reject(id: string, body: ResolveApprovalBody) {
    return this.http.request<ApprovalRequest>({
      method: "POST",
      path: `/v1/approvals/${id}/reject`,
      body,
    });
  }
  cancel(id: string, body: CancelApprovalBody = {}) {
    return this.http.request<ApprovalRequest>({
      method: "POST",
      path: `/v1/approvals/${id}/cancel`,
      body,
    });
  }
  expire(id: string) {
    return this.http.request<ApprovalRequest>({
      method: "POST",
      path: `/v1/approvals/${id}/expire`,
    });
  }
}
