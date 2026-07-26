import type {
  ApprovalRequest,
  CancelApprovalBody,
  Page,
  RequestApprovalBody,
  ResolveApprovalBody,
} from "@friday/contracts";
import { validateApproval, validateApprovalPage } from "@friday/contracts";
import type { FridayHttpClient } from "../http";
export interface ListApprovalsParams {
  limit?: number;
  cursor?: string;
}
/** Literal forwards only: approval decisions remain caller-owned. */
export class ApprovalsResource {
  constructor(private readonly http: FridayHttpClient) {}
  request(runId: string, body: RequestApprovalBody) {
    return this.http.requestJson<ApprovalRequest>({
      method: "POST",
      path: `/v1/runs/${runId}/approvals`,
      body,
      validate: validateApproval,
    });
  }
  get(id: string) {
    return this.http.requestJson<ApprovalRequest>({
      method: "GET",
      path: `/v1/approvals/${id}`,
      validate: validateApproval,
    });
  }
  listForRun(runId: string, p: ListApprovalsParams = {}) {
    return this.http.requestJson<Page<ApprovalRequest>>({
      method: "GET",
      path: `/v1/runs/${runId}/approvals`,
      query: { limit: p.limit, cursor: p.cursor },
      validate: validateApprovalPage,
    });
  }
  approve(id: string, body: ResolveApprovalBody) {
    return this.http.requestJson<ApprovalRequest>({
      method: "POST",
      path: `/v1/approvals/${id}/approve`,
      body,
      validate: validateApproval,
    });
  }
  reject(id: string, body: ResolveApprovalBody) {
    return this.http.requestJson<ApprovalRequest>({
      method: "POST",
      path: `/v1/approvals/${id}/reject`,
      body,
      validate: validateApproval,
    });
  }
  cancel(id: string, body: CancelApprovalBody = {}) {
    return this.http.requestJson<ApprovalRequest>({
      method: "POST",
      path: `/v1/approvals/${id}/cancel`,
      body,
      validate: validateApproval,
    });
  }
  expire(id: string) {
    return this.http.requestJson<ApprovalRequest>({
      method: "POST",
      path: `/v1/approvals/${id}/expire`,
      validate: validateApproval,
    });
  }
}
