import type { SkillFeedback } from "@friday/contracts";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { friday } from "../friday-client";

export type SkillFeedbackRating = SkillFeedback["rating"];

export interface AddRunSkillFeedbackInput {
  rating: SkillFeedbackRating;
  note?: string;
  created_by: string;
}

export const runSkillFeedbackQueryKey = (runId: string, skillId: string) =>
  ["run-skill-feedback", runId, skillId] as const;

export function useRunSkillFeedback(runId: string, skillId: string) {
  return useQuery({
    queryKey: runSkillFeedbackQueryKey(runId, skillId),
    queryFn: () => friday.runs.listSkillFeedback(runId, skillId),
    retry: false,
  });
}

export function useAddRunSkillFeedback(runId: string, skillId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: AddRunSkillFeedbackInput) =>
      friday.runs.addSkillFeedback(runId, skillId, input),
    retry: false,
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: runSkillFeedbackQueryKey(runId, skillId),
        exact: true,
      }),
  });
}
