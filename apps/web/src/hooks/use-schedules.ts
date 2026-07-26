import type { CreateScheduleBody } from "@friday/sdk";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { friday } from "../friday-client";
import { loadAllPages } from "./use-all-pages";

export const schedulesQueryKey = (taskId: string) =>
  ["schedules", taskId] as const;
export const scheduleFiresQueryKey = (taskId: string, scheduleId: string) =>
  ["schedule-fires", taskId, scheduleId] as const;

export function useSchedules(taskId: string) {
  return useQuery({
    queryKey: schedulesQueryKey(taskId),
    queryFn: () =>
      loadAllPages((cursor) =>
        friday.schedules.list(taskId, { limit: 100, cursor }),
      ),
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
    queryFn: () =>
      loadAllPages((cursor) =>
        friday.schedules.fires(taskId, scheduleId!, { limit: 100, cursor }),
      ),
  });
}
