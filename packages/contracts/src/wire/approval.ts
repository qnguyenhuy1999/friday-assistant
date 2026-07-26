import type { JsonValue } from "./json-value";
/** Mirrors `apps/api/schemas/approvals.py`. */
export type ApprovalCategory =
  | "tool_execution"
  | "filesystem_write"
  | "network_access"
  | "computer_use"
  | "other";
export type ApprovalStatus =
  "pending" | "approved" | "rejected" | "cancelled" | "expired";
export interface ApprovalRequest {
  approval_id: string;
  run_id: string;
  step_id: string | null;
  category: ApprovalCategory;
  summary: string;
  reason: string;
  requested_action: string;
  requested_input: JsonValue;
  status: ApprovalStatus;
  requested_at: string;
  expires_at: string | null;
  resolved_at: string | null;
  resolution_note: string | null;
  resolver: string | null;
  authorization_fingerprint: string | null;
  consumed_at: string | null;
}
export interface RequestApprovalBody {
  category: ApprovalCategory;
  summary: string;
  reason: string;
  requested_action: string;
  requested_input?: JsonValue;
  step_id?: string;
  expires_at?: string;
}
export interface ResolveApprovalBody {
  resolver: string;
  resolution_note?: string;
}
export interface CancelApprovalBody {
  resolution_note?: string;
}
