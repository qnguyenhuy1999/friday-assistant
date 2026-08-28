import type {
  CreateWorkflowBody,
  CreateWorkflowRevisionBody,
} from "@friday/contracts";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { friday } from "../friday-client";

export const WORKFLOW_REVISION_PAGE_SIZE = 10;
export const workflowsQueryKey = ["workflows"] as const;
export const workflowQueryKey = (workflowId: string) =>
  ["workflow", workflowId] as const;
export const workflowRevisionsQueryKey = (workflowId: string) =>
  ["workflow-revisions", workflowId] as const;

function invalidateWorkflow(
  queryClient: ReturnType<typeof useQueryClient>,
  workflowId: string,
) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: workflowsQueryKey }),
    queryClient.invalidateQueries({ queryKey: workflowQueryKey(workflowId) }),
    queryClient.invalidateQueries({
      queryKey: workflowRevisionsQueryKey(workflowId),
    }),
  ]);
}

export function useWorkflows() {
  return useInfiniteQuery({
    queryKey: workflowsQueryKey,
    queryFn: ({ pageParam }) =>
      friday.workflows.list({ limit: 25, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
}

export function useWorkflow(workflowId: string) {
  return useQuery({
    queryKey: workflowQueryKey(workflowId),
    queryFn: () => friday.workflows.get(workflowId),
  });
}

export function useWorkflowRevisions(workflowId: string) {
  return useInfiniteQuery({
    queryKey: workflowRevisionsQueryKey(workflowId),
    queryFn: ({ pageParam }) =>
      friday.workflows.listRevisionsPage(workflowId, {
        limit: WORKFLOW_REVISION_PAGE_SIZE,
        beforeVersion: pageParam,
      }),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (page) =>
      page.length === WORKFLOW_REVISION_PAGE_SIZE
        ? page.at(-1)?.version
        : undefined,
  });
}

export function useCreateWorkflow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateWorkflowBody) => friday.workflows.create(input),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: workflowsQueryKey }),
  });
}

export function useCreateWorkflowRevision(workflowId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateWorkflowRevisionBody) =>
      friday.workflows.createRevision(workflowId, input),
    onSuccess: () => invalidateWorkflow(queryClient, workflowId),
  });
}

export function useActivateWorkflowRevision(workflowId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (revisionId: string) =>
      friday.workflows.activateRevision(workflowId, revisionId),
    onSuccess: () => invalidateWorkflow(queryClient, workflowId),
  });
}

export function useWorkflowLifecycle(workflowId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (action: "disable" | "archive") =>
      friday.workflows[action](workflowId),
    onSuccess: () => invalidateWorkflow(queryClient, workflowId),
  });
}
