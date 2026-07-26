import { useQuery } from "@tanstack/react-query";
import { friday } from "../friday-client";
export const runToolInvocationsQueryKey = (id: string) =>
  ["run-tool-invocations", id] as const;
export function useRunToolInvocations(id: string) {
  return useQuery({
    queryKey: runToolInvocationsQueryKey(id),
    queryFn: () => friday.toolInvocations.listForRun(id, { limit: 100 }),
  });
}
