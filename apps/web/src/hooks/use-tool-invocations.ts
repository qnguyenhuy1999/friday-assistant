import { useQuery } from "@tanstack/react-query";
import { friday } from "../friday-client";
import { loadAllPages } from "./use-all-pages";
export const runToolInvocationsQueryKey = (id: string) =>
  ["run-tool-invocations", id] as const;
export function useRunToolInvocations(id: string) {
  return useQuery({
    queryKey: runToolInvocationsQueryKey(id),
    queryFn: () =>
      loadAllPages((cursor) =>
        friday.toolInvocations.listForRun(id, { limit: 100, cursor }),
      ),
  });
}
