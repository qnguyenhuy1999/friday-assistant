import type { ApprovalRequest } from "@friday/contracts";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApprovalDetail } from "./approval-detail";

const approval: ApprovalRequest = {
  approval_id: "a-1",
  run_id: "r-1",
  step_id: null,
  category: "computer_use",
  summary: "Click Send in Messages",
  reason: "Sending a message on the user's behalf requires explicit sign-off",
  requested_action: "computer.click",
  requested_input: {
    pid: 844,
    window_id: 10725,
    element: { role: "button", label: "Send" },
  },
  status: "pending",
  requested_at: "2026-07-26T00:00:02Z",
  expires_at: null,
  resolved_at: null,
  resolution_note: null,
  resolver: null,
  authorization_fingerprint: null,
  consumed_at: null,
};

describe("ApprovalDetail", () => {
  it("renders the authorization intent verbatim", () => {
    render(
      <ApprovalDetail
        approval={approval}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />,
    );
    const detail = screen.getByRole("article", { name: "Approval detail" });
    expect(detail).toHaveTextContent("computer_use");
    expect(detail).toHaveTextContent("Click Send in Messages");
    expect(detail).toHaveTextContent(approval.reason);
    expect(detail).toHaveTextContent("computer.click");
    expect(detail).toHaveTextContent("r-1");
    expect(detail).toHaveTextContent("2026-07-26T00:00:02Z");
    // requested_input is shown as literal JSON — never paraphrased or summarized.
    expect(screen.getByText(/"pid": 844/)).toBeInTheDocument();
    expect(screen.getByText(/"label": "Send"/)).toBeInTheDocument();
  });

  it("does not approve until Approve is explicitly clicked", async () => {
    const onApprove = vi.fn().mockResolvedValue(undefined);
    render(
      <ApprovalDetail
        approval={approval}
        onApprove={onApprove}
        onReject={vi.fn()}
      />,
    );
    expect(onApprove).not.toHaveBeenCalled();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Your name or email"), "patrick");
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(onApprove).toHaveBeenCalledWith("patrick", undefined);
  });

  it("forwards an optional resolution note when one is given", async () => {
    const onApprove = vi.fn().mockResolvedValue(undefined);
    render(
      <ApprovalDetail
        approval={approval}
        onApprove={onApprove}
        onReject={vi.fn()}
      />,
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Your name or email"), "patrick");
    await user.type(screen.getByLabelText("Note (optional)"), "confirmed live");
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(onApprove).toHaveBeenCalledWith("patrick", "confirmed live");
  });

  it("refuses to resolve without a resolver name", async () => {
    const onApprove = vi.fn();
    const onReject = vi.fn();
    render(
      <ApprovalDetail
        approval={approval}
        onApprove={onApprove}
        onReject={onReject}
      />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Approve" }));
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(onApprove).not.toHaveBeenCalled();
    expect(onReject).not.toHaveBeenCalled();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter your name or email",
    );
  });

  it("surfaces a failure instead of treating it as success", async () => {
    const onReject = vi.fn().mockRejectedValue(new Error("boom"));
    render(
      <ApprovalDetail
        approval={approval}
        onApprove={vi.fn()}
        onReject={onReject}
      />,
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Your name or email"), "patrick");
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The request failed — the approval's status has not changed.",
    );
  });

  it("removes the decision controls once the approval is no longer pending", () => {
    render(
      <ApprovalDetail
        approval={{ ...approval, status: "approved" }}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />,
    );
    expect(
      screen.queryByRole("button", { name: "Approve" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Reject" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/can no longer be acted on/)).toBeInTheDocument();
  });

  it("disables both decisions while a resolution is pending", () => {
    render(
      <ApprovalDetail
        approval={approval}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        isPending
      />,
    );
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
  });
});
