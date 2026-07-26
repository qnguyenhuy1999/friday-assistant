import { useState } from "react";
import { ApprovalDetail } from "../components/approval-detail";
import {
  useApproveApproval,
  useRejectApproval,
  useRunApprovals,
} from "../hooks/use-approvals";
export function ApprovalsPage({
  runId,
  onBackToRun,
}: {
  runId: string;
  onBackToRun: () => void;
}) {
  const { data, isLoading, isError } = useRunApprovals(runId);
  const approve = useApproveApproval(runId);
  const reject = useRejectApproval(runId);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  if (isLoading) return <p>Loading approvals…</p>;
  if (isError) return <p role="alert">Failed to load approvals.</p>;
  const selected = data?.items.find((a) => a.approval_id === selectedId);
  return (
    <section>
      <h2>Approvals</h2>
      <button onClick={onBackToRun}>Back to run</button>
      <ul>
        {data?.items.map((a) => (
          <li key={a.approval_id}>
            <button onClick={() => setSelectedId(a.approval_id)}>
              {a.summary} — {a.status}
            </button>
          </li>
        ))}
      </ul>
      {selected && (
        <ApprovalDetail
          approval={selected}
          onApprove={async (resolver, resolution_note) => {
            await approve.mutateAsync({
              approvalId: selected.approval_id,
              input: { resolver, resolution_note },
            });
          }}
          onReject={async (resolver, resolution_note) => {
            await reject.mutateAsync({
              approvalId: selected.approval_id,
              input: { resolver, resolution_note },
            });
          }}
        />
      )}
    </section>
  );
}
