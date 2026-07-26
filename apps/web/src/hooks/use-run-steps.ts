import { useQuery } from "@tanstack/react-query";
import { friday } from "../friday-client";
export const runStepsQueryKey = (id: string) => ["run-steps", id] as const;
export function useRunSteps(id: string) {
  return useQuery({
    queryKey: runStepsQueryKey(id),
    queryFn: () => friday.steps.listForRun(id, { limit: 100 }),
  });
}
