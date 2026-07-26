import { useQuery } from "@tanstack/react-query";
import { friday } from "../friday-client";
import { loadAllPages } from "./use-all-pages";
export const runArtifactsQueryKey = (id: string) =>
  ["run-artifacts", id] as const;
export function useRunArtifacts(id: string) {
  return useQuery({
    queryKey: runArtifactsQueryKey(id),
    queryFn: () =>
      loadAllPages((cursor) =>
        friday.artifacts.listForRun(id, { limit: 100, cursor }),
      ),
  });
}
