import { FridayApiError } from "@friday/sdk";
import { useQuery } from "@tanstack/react-query";
import { friday } from "../friday-client";

export const runWorkflowQueryKey = (runId: string) =>
  ["run-workflow", runId] as const;
export const runWorkflowNodesQueryKey = (runId: string) =>
  ["run-workflow-nodes", runId] as const;

export function isMissingWorkflowExecution(error: unknown): boolean {
  return error instanceof FridayApiError && error.status === 404;
}

export function useRunWorkflow(runId: string) {
  return useQuery({
    queryKey: runWorkflowQueryKey(runId),
    queryFn: () => friday.runs.getWorkflow(runId),
    retry: false,
  });
}

export function useRunWorkflowNodes(runId: string, enabled: boolean) {
  return useQuery({
    queryKey: runWorkflowNodesQueryKey(runId),
    queryFn: () => friday.runs.getWorkflowNodes(runId),
    enabled,
    retry: false,
  });
}
