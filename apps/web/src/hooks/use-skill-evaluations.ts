import type {
  CreateEvaluationSuiteBody,
  RunEvaluationBody,
} from "@friday/contracts";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { friday } from "../friday-client";

export const evaluationSuitesQueryKey = (skillId: string) =>
  ["skill-evaluation-suites", skillId] as const;
export const evaluationSuiteQueryKey = (suiteId: string) =>
  ["skill-evaluation-suite", suiteId] as const;
export const evaluationRunQueryKey = (runId: string) =>
  ["skill-evaluation-run", runId] as const;

export function useSkillEvaluationSuites(skillId: string) {
  return useQuery({
    queryKey: evaluationSuitesQueryKey(skillId),
    queryFn: () => friday.skills.listEvaluationSuites(skillId),
    retry: false,
  });
}

export function useSkillEvaluationSuite(suiteId: string | null) {
  return useQuery({
    queryKey: evaluationSuiteQueryKey(suiteId ?? "none"),
    queryFn: () => friday.skills.getEvaluationSuite(suiteId!),
    enabled: suiteId !== null,
    retry: false,
  });
}

export function useCreateSkillEvaluationSuite(skillId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateEvaluationSuiteBody) =>
      friday.skills.createEvaluationSuite(skillId, body),
    retry: false,
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: evaluationSuitesQueryKey(skillId),
      }),
  });
}

export function useRunSkillEvaluation(suiteId: string) {
  return useMutation({
    mutationFn: (body: RunEvaluationBody) =>
      friday.skills.runEvaluation(suiteId, body),
    retry: false,
  });
}

export function useSkillEvaluationRun(runId: string | null) {
  return useQuery({
    queryKey: evaluationRunQueryKey(runId ?? "none"),
    queryFn: () => friday.skills.getEvaluationRun(runId!),
    enabled: runId !== null,
    retry: false,
  });
}
