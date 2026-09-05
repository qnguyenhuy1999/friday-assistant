import { useQuery } from "@tanstack/react-query";
import { friday } from "../friday-client";

export const runSkillsQueryKey = (runId: string) =>
  ["run-skills", runId] as const;

export function useRunSkills(runId: string) {
  return useQuery({
    queryKey: runSkillsQueryKey(runId),
    queryFn: () => friday.runs.getSkills(runId),
    retry: false,
  });
}
