import { useQuery } from "@tanstack/react-query";
import { friday } from "../friday-client";

export const runAgentQueryKey = (runId: string) =>
  ["run-agent", runId] as const;

export function useRunAgent(runId: string) {
  return useQuery({
    queryKey: runAgentQueryKey(runId),
    queryFn: () => friday.runs.getAgent(runId),
  });
}
