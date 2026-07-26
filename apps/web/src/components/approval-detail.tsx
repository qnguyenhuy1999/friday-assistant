import type { ApprovalRequest } from "@friday/contracts";
import { useState } from "react";
export function ApprovalDetail({
  approval,
  onApprove,
  onReject,
}: {
  approval: ApprovalRequest;
  onApprove: (resolver: string, note?: string) => Promise<void>;
  onReject: (resolver: string, note?: string) => Promise<void>;
}) {
  const [resolver, setResolver] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const decide = async (fn: (name: string, note?: string) => Promise<void>) => {
    setError(null);
    const name = resolver.trim();
    if (!name) {
      setError("Enter your name or email before approving or rejecting.");
      return;
    }
    try {
      await fn(name, note.trim() || undefined);
    } catch {
      setError("The request failed — the approval's status has not changed.");
    }
  };
  return (
    <article aria-label="Approval detail">
      <dl>
        <dt>Category</dt>
        <dd>{approval.category}</dd>
        <dt>Status</dt>
        <dd>{approval.status}</dd>
        <dt>Summary</dt>
        <dd>{approval.summary}</dd>
        <dt>Reason</dt>
        <dd>{approval.reason}</dd>
        <dt>Requested action</dt>
        <dd>
          <code>{approval.requested_action}</code>
        </dd>
        <dt>Requested input</dt>
        <dd>
          <pre>{JSON.stringify(approval.requested_input, null, 2)}</pre>
        </dd>
        <dt>Originating run</dt>
        <dd>{approval.run_id}</dd>
        <dt>Originating step</dt>
        <dd>{approval.step_id ?? "—"}</dd>
        <dt>Requested at</dt>
        <dd>{approval.requested_at}</dd>
        <dt>Expires at</dt>
        <dd>{approval.expires_at ?? "—"}</dd>
      </dl>
      {approval.status === "pending" ? (
        <form onSubmit={(e) => e.preventDefault()}>
          <label htmlFor="resolver">Your name or email</label>
          <input
            id="resolver"
            value={resolver}
            onChange={(e) => setResolver(e.target.value)}
          />
          <label htmlFor="resolution-note">Note (optional)</label>
          <input
            id="resolution-note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          {error && <p role="alert">{error}</p>}
          <button type="button" onClick={() => decide(onApprove)}>
            Approve
          </button>
          <button type="button" onClick={() => decide(onReject)}>
            Reject
          </button>
        </form>
      ) : (
        <p>This approval is {approval.status} and can no longer be acted on.</p>
      )}
    </article>
  );
}
