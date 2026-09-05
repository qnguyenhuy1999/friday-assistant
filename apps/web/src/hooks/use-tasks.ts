import type { CreateTaskBody } from "@friday/contracts";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { friday } from "../friday-client";
export const tasksQueryKey = ["tasks"] as const;

export const taskQueryKey = (taskId: string) => ["task", taskId] as const;
export const taskAgentBindingQueryKey = (taskId: string) =>
  ["task-agent-binding", taskId] as const;
export const taskWorkflowBindingQueryKey = (taskId: string) =>
  ["task-workflow-binding", taskId] as const;
export const taskSkillsQueryKey = (taskId: string) =>
  ["task-skills", taskId] as const;

export function useTasks() {
  return useInfiniteQuery({
    queryKey: tasksQueryKey,
    queryFn: ({ pageParam }) =>
      friday.tasks.list({ limit: 25, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
}

export function useTask(taskId: string) {
  return useQuery({
    queryKey: taskQueryKey(taskId),
    queryFn: () => friday.tasks.get(taskId),
  });
}

export function useTaskAgentBinding(taskId: string) {
  return useQuery({
    queryKey: taskAgentBindingQueryKey(taskId),
    queryFn: () => friday.tasks.getAgent(taskId),
    retry: false,
  });
}

export function useTaskWorkflowBinding(taskId: string) {
  return useQuery({
    queryKey: taskWorkflowBindingQueryKey(taskId),
    queryFn: () => friday.tasks.getWorkflow(taskId),
    retry: false,
  });
}

export function useTaskSkills(taskId: string) {
  return useQuery({
    queryKey: taskSkillsQueryKey(taskId),
    queryFn: () => friday.tasks.listSkills(taskId),
    retry: false,
  });
}

function invalidateTaskExecutionTarget(
  queryClient: ReturnType<typeof useQueryClient>,
  taskId: string,
) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: tasksQueryKey }),
    queryClient.invalidateQueries({ queryKey: taskQueryKey(taskId) }),
    queryClient.invalidateQueries({
      queryKey: taskAgentBindingQueryKey(taskId),
    }),
    queryClient.invalidateQueries({
      queryKey: taskWorkflowBindingQueryKey(taskId),
    }),
  ]);
}

export function usePutTaskAgent(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (agentId: string) =>
      friday.tasks.putAgent(taskId, { agent_id: agentId }),
    onSuccess: () => invalidateTaskExecutionTarget(queryClient, taskId),
  });
}

export function useClearTaskAgent(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => friday.tasks.putAgent(taskId, { agent_id: null }),
    onSuccess: () => invalidateTaskExecutionTarget(queryClient, taskId),
  });
}

export function useBindTaskWorkflow(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (workflowId: string) =>
      friday.tasks.bindWorkflow(taskId, workflowId),
    onSuccess: () => invalidateTaskExecutionTarget(queryClient, taskId),
  });
}

export function useUnbindTaskWorkflow(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => friday.tasks.unbindWorkflow(taskId),
    onSuccess: () => invalidateTaskExecutionTarget(queryClient, taskId),
  });
}

function invalidateTaskSkillComposition(
  queryClient: ReturnType<typeof useQueryClient>,
  taskId: string,
) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: tasksQueryKey }),
    queryClient.invalidateQueries({ queryKey: taskQueryKey(taskId) }),
    queryClient.invalidateQueries({ queryKey: taskSkillsQueryKey(taskId) }),
  ]);
}

export function useReplaceTaskSkills(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (skillIds: string[]) =>
      friday.tasks.replaceSkills(taskId, skillIds),
    retry: false,
    onSuccess: () => invalidateTaskSkillComposition(queryClient, taskId),
  });
}

export function useCreateTask() {
  const q = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateTaskBody) => friday.tasks.create(input),
    onSuccess: () => q.invalidateQueries({ queryKey: tasksQueryKey }),
  });
}
export function useStartRun() {
  const q = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => friday.tasks.startRun(id),
    onSuccess: (_response, taskId) =>
      Promise.all([
        q.invalidateQueries({ queryKey: tasksQueryKey }),
        q.invalidateQueries({ queryKey: taskQueryKey(taskId) }),
      ]),
  });
}
