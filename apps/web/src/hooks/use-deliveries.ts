import { useQuery } from "@tanstack/react-query";
import { friday } from "../friday-client";

export const runDeliveriesQueryKey = (runId: string) =>
  ["run-deliveries", runId] as const;

export function useRunDeliveries(runId: string) {
  return useQuery({
    queryKey: runDeliveriesQueryKey(runId),
    queryFn: () => friday.deliveries.listForRun(runId),
    refetchInterval: 5000,
  });
}
