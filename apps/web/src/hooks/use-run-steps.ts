import { useQuery } from "@tanstack/react-query";
import { friday } from "../friday-client";
import { loadAllPages } from "./use-all-pages";
export const runStepsQueryKey = (id: string) => ["run-steps", id] as const;
export function useRunSteps(id: string) {
  return useQuery({
    queryKey: runStepsQueryKey(id),
    queryFn: () =>
      loadAllPages((cursor) =>
        friday.steps.listForRun(id, { limit: 100, cursor }),
      ),
  });
}
