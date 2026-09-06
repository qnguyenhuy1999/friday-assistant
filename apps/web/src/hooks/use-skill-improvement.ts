import type {
  ImprovementProposal,
  SkillImprovementPolicy,
  SkillImprovementPolicyBody,
} from "@friday/contracts";
import { FridayApiError } from "@friday/sdk";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { friday } from "../friday-client";

export const skillImprovementPolicyQueryKey = (skillId: string) =>
  ["skill-improvement-policy", skillId] as const;
export const skillImprovementProposalsQueryKey = (skillId: string) =>
  ["skill-improvement-proposals", skillId] as const;
export const skillImprovementProposalQueryKey = (proposalId: string) =>
  ["skill-improvement-proposal", proposalId] as const;
export const skillEvidenceSnapshotQueryKey = (snapshotId: string) =>
  ["skill-evidence-snapshot", snapshotId] as const;
export const skillProposalEvaluationQueryKey = (proposalId: string) =>
  ["skill-proposal-evaluation", proposalId] as const;

export function isNotFoundError(error: unknown): boolean {
  return error instanceof FridayApiError && error.status === 404;
}

export function useSkillImprovementPolicy(skillId: string) {
  return useQuery({
    queryKey: skillImprovementPolicyQueryKey(skillId),
    queryFn: () => friday.skills.getImprovementPolicy(skillId),
    retry: false,
  });
}

export function useSkillImprovementProposals(skillId: string) {
  return useQuery({
    queryKey: skillImprovementProposalsQueryKey(skillId),
    queryFn: () => friday.skills.listProposals(skillId),
    retry: false,
  });
}

export function useSkillImprovementProposal(proposalId: string) {
  return useQuery({
    queryKey: skillImprovementProposalQueryKey(proposalId),
    queryFn: () => friday.skills.getProposal(proposalId),
    retry: false,
  });
}

export function useSkillEvidenceSnapshot(snapshotId: string | null) {
  return useQuery({
    queryKey: skillEvidenceSnapshotQueryKey(snapshotId ?? "none"),
    queryFn: () => friday.skills.getEvidenceSnapshot(snapshotId!),
    enabled: snapshotId !== null,
    retry: false,
  });
}

export function useSkillProposalEvaluation(proposalId: string | null) {
  return useQuery({
    queryKey: skillProposalEvaluationQueryKey(proposalId ?? "none"),
    queryFn: () => friday.skills.getProposalEvaluation(proposalId!),
    enabled: proposalId !== null,
    retry: false,
  });
}

export function useSaveSkillImprovementPolicy(
  skillId: string,
  knownSuiteIds: string[],
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: SkillImprovementPolicyBody) => {
      const response = await friday.skills.putImprovementPolicy(skillId, body);
      if (
        response.skill_id !== skillId ||
        !knownSuiteIds.includes(response.evaluation_suite_id) ||
        response.generator_version !== "brain-candidate-generator-v2" ||
        response.comparison_policy_version !== "comparison-v1" ||
        response.max_open_proposals !== 1
      ) {
        throw new Error("Improvement policy provenance could not be verified.");
      }
      return response;
    },
    retry: false,
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: skillImprovementPolicyQueryKey(skillId),
      }),
  });
}

export function useRunSkillImprovementPolicyNow(skillId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => friday.skills.runImprovementPolicyNow(skillId),
    retry: false,
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({
          queryKey: skillImprovementPolicyQueryKey(skillId),
        }),
        queryClient.invalidateQueries({
          queryKey: skillImprovementProposalsQueryKey(skillId),
        }),
      ]),
  });
}

export function useCancelSkillImprovementProposal(
  proposalId: string,
  skillId: string,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const response = await friday.skills.cancelProposal(proposalId);
      if (
        response.id !== proposalId ||
        response.skill_id !== skillId ||
        response.status !== "cancelled"
      ) {
        throw new Error(
          "Improvement proposal provenance could not be verified.",
        );
      }
      return response;
    },
    retry: false,
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({
          queryKey: skillImprovementProposalQueryKey(proposalId),
        }),
        queryClient.invalidateQueries({
          queryKey: skillImprovementProposalsQueryKey(skillId),
        }),
      ]),
  });
}

export type { ImprovementProposal, SkillImprovementPolicy };
