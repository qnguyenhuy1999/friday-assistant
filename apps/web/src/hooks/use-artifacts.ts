import { useQuery } from "@tanstack/react-query";
import { friday } from "../friday-client";
export const runArtifactsQueryKey = (id: string) =>
  ["run-artifacts", id] as const;
export function useRunArtifacts(id: string) {
  return useQuery({
    queryKey: runArtifactsQueryKey(id),
    queryFn: () => friday.artifacts.listForRun(id, { limit: 100 }),
  });
}
