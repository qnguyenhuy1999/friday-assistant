import type {
  ApprovalRequest,
  CancelApprovalBody,
  RequestApprovalBody,
  ResolveApprovalBody,
} from "../../index";

export const requestApprovalBodyExample: RequestApprovalBody = {
  category: "computer_use",
  summary: "Click Send in Messages",
  reason: "Sending a message on the user's behalf requires explicit sign-off",
  requested_action: "computer.click",
  requested_input: {
    pid: 844,
    window_id: 10725,
    element: { role: "button", label: "Send" },
  },
};

export const approvalExample: ApprovalRequest = {
  approval_id: "8f14e45f-ceea-467e-adde-3f4694a0dead",
  run_id: "8f14e45f-ceea-467e-adde-3f4694a05678",
  step_id: null,
  category: "computer_use",
  summary: requestApprovalBodyExample.summary,
  reason: requestApprovalBodyExample.reason,
  requested_action: requestApprovalBodyExample.requested_action,
  requested_input: requestApprovalBodyExample.requested_input ?? null,
  status: "pending",
  requested_at: "2026-07-26T00:00:02Z",
  expires_at: null,
  resolved_at: null,
  resolution_note: null,
  resolver: null,
  authorization_fingerprint: null,
  consumed_at: null,
  subject_kind: "run",
  subject_id: "8f14e45f-ceea-467e-adde-3f4694a05678",
};

export const resolveApprovalBodyExample: ResolveApprovalBody = {
  resolver: "patrick.le@siliconstack.com.au",
  resolution_note: "confirmed with the user over Slack",
};

export const cancelApprovalBodyExample: CancelApprovalBody = {
  resolution_note: "task cancelled",
};
