import type { CreateScheduleBody } from "@friday/sdk";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { friday } from "../friday-client";

export const schedulesQueryKey = (taskId: string) =>
  ["schedules", taskId] as const;
export const scheduleFiresQueryKey = (taskId: string, scheduleId: string) =>
  ["schedule-fires", taskId, scheduleId] as const;

export function useSchedules(taskId: string) {
  return useQuery({
    queryKey: schedulesQueryKey(taskId),
    queryFn: () => friday.schedules.list(taskId, { limit: 100 }),
  });
}

export function useCreateSchedule(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateScheduleBody) =>
      friday.schedules.create(taskId, input),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: schedulesQueryKey(taskId) }),
  });
}

export function useScheduleControl(taskId: string, scheduleId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (action: "pause" | "resume" | "cancel") =>
      friday.schedules[action](taskId, scheduleId),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: schedulesQueryKey(taskId) }),
  });
}

export function useScheduleFires(taskId: string, scheduleId: string | null) {
  return useQuery({
    queryKey: scheduleFiresQueryKey(taskId, scheduleId ?? "none"),
    enabled: scheduleId !== null,
    queryFn: () => friday.schedules.fires(taskId, scheduleId!, { limit: 100 }),
  });
}
