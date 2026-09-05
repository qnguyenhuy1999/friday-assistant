import type {
  CreateSkillBody,
  CreateSkillRevisionBody,
} from "@friday/contracts";
import {
  useInfiniteQuery,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { friday } from "../friday-client";

export const SKILL_PAGE_SIZE = 25;
export const SKILL_REVISION_PAGE_SIZE = 25;
export const skillsQueryKey = ["skills"] as const;
export const skillQueryKey = (skillId: string) => ["skill", skillId] as const;
export const skillRevisionQueryKey = (
  skillId: string,
  revisionId: string | null,
) => ["skill-revision", skillId, revisionId ?? "none"] as const;
export const skillRevisionsQueryKey = (skillId: string) =>
  ["skill-revisions", skillId] as const;
export const skillUsageQueryKey = (skillId: string) =>
  ["skill-usage", skillId] as const;

function invalidateSkill(
  queryClient: ReturnType<typeof useQueryClient>,
  skillId: string,
) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: skillsQueryKey }),
    queryClient.invalidateQueries({ queryKey: skillQueryKey(skillId) }),
    queryClient.invalidateQueries({
      queryKey: skillRevisionsQueryKey(skillId),
    }),
  ]);
}

export function useSkills() {
  return useInfiniteQuery({
    queryKey: skillsQueryKey,
    queryFn: ({ pageParam }) =>
      friday.skills.list({ limit: SKILL_PAGE_SIZE, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
}

export function useSkill(skillId: string | null) {
  return useQuery({
    queryKey: skillQueryKey(skillId ?? "none"),
    queryFn: () => friday.skills.get(skillId!),
    enabled: skillId !== null,
  });
}

export function useSkillUsage(skillId: string) {
  return useQuery({
    queryKey: skillUsageQueryKey(skillId),
    queryFn: () => friday.skills.listUsage(skillId),
    retry: false,
  });
}

/**
 * Read the current metadata for a bounded set of persisted Task bindings.
 * Task bindings are capped by the server at 16, so this exact fan-out never
 * requires loading the whole paginated Skill registry.
 */
export function useSkillDetails(skillIds: string[]) {
  return useQueries({
    queries: skillIds.map((skillId) => ({
      queryKey: skillQueryKey(skillId),
      queryFn: () => friday.skills.get(skillId),
      retry: false,
    })),
  });
}

export function useSkillRevision(skillId: string, revisionId: string | null) {
  return useQuery({
    queryKey: skillRevisionQueryKey(skillId, revisionId),
    queryFn: () => friday.skills.getRevision(skillId, revisionId!),
    enabled: revisionId !== null,
  });
}

export function useSkillRevisions(skillId: string) {
  return useInfiniteQuery({
    queryKey: skillRevisionsQueryKey(skillId),
    queryFn: ({ pageParam }) =>
      friday.skills.listRevisionsPage(skillId, {
        limit: SKILL_REVISION_PAGE_SIZE,
        beforeVersion: pageParam,
      }),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (page) =>
      page.length === SKILL_REVISION_PAGE_SIZE
        ? page.at(-1)?.version
        : undefined,
  });
}

export function useCreateSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateSkillBody) => friday.skills.create(input),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: skillsQueryKey }),
  });
}

export function useCreateSkillRevision(skillId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateSkillRevisionBody) =>
      friday.skills.createRevision(skillId, input),
    onSuccess: () => invalidateSkill(queryClient, skillId),
  });
}

export function useActivateSkillRevision(skillId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (revisionId: string) =>
      friday.skills.activateRevision(skillId, revisionId),
    onSuccess: () => invalidateSkill(queryClient, skillId),
  });
}

export function useSkillLifecycle(skillId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (action: "disable" | "archive") =>
      friday.skills[action](skillId),
    onSuccess: () => invalidateSkill(queryClient, skillId),
  });
}
