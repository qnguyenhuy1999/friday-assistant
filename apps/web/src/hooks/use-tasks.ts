import type { CreateTaskBody } from "@friday/contracts";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { friday } from "../friday-client";
export const tasksQueryKey = ["tasks"] as const;
export function useTasks() {
  return useQuery({
    queryKey: tasksQueryKey,
    queryFn: () => friday.tasks.list({ limit: 25 }),
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
    onSuccess: () => q.invalidateQueries({ queryKey: tasksQueryKey }),
  });
}
