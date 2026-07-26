import type { ResolveApprovalBody } from "@friday/contracts";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { friday } from "../friday-client";
import { runQueryKey } from "./use-run";
export const runApprovalsQueryKey = (id: string) =>
  ["run-approvals", id] as const;
export function useRunApprovals(id: string) {
  return useQuery({
    queryKey: runApprovalsQueryKey(id),
    queryFn: () => friday.approvals.listForRun(id, { limit: 25 }),
  });
}
type ResolveInput = { approvalId: string; input: ResolveApprovalBody };
/** Shared mutation shape for both decisions; named as a hook because it calls hooks. */
function useResolveApproval(
  runId: string,
  fn: (id: string, input: ResolveApprovalBody) => Promise<unknown>,
) {
  const q = useQueryClient();
  return useMutation({
    mutationFn: ({ approvalId, input }: ResolveInput) => fn(approvalId, input),
    onSuccess: () => {
      q.invalidateQueries({ queryKey: runApprovalsQueryKey(runId) });
      q.invalidateQueries({ queryKey: runQueryKey(runId) });
    },
  });
}
export function useApproveApproval(runId: string) {
  return useResolveApproval(runId, (id, input) =>
    friday.approvals.approve(id, input),
  );
}
export function useRejectApproval(runId: string) {
  return useResolveApproval(runId, (id, input) =>
    friday.approvals.reject(id, input),
  );
}
