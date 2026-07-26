import type { Run, RunStatus } from "@friday/contracts";
import { useQuery } from "@tanstack/react-query";
import { friday } from "../friday-client";
const terminal: RunStatus[] = ["succeeded", "failed", "cancelled"];
export const isTerminalRunStatus = (status: RunStatus) =>
  terminal.includes(status);
export const runRefetchIntervalMs = (run: Run | undefined): number | false =>
  !run || !isTerminalRunStatus(run.status) ? 5000 : false;
export const runQueryKey = (id: string) => ["run", id] as const;
export function useRun(id: string) {
  return useQuery({
    queryKey: runQueryKey(id),
    queryFn: () => friday.runs.get(id),
    refetchInterval: (query) => runRefetchIntervalMs(query.state.data),
  });
}
