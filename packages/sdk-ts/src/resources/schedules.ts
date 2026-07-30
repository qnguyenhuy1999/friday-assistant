import type {
  Page,
  Schedule,
  ScheduleDeliveryPolicy,
  ScheduleFire,
} from "@friday/contracts";
import {
  validateSchedule,
  validateScheduleFirePage,
  validateSchedulePage,
  validateScheduleDeliveryPolicy,
} from "@friday/contracts";
import type { FridayHttpClient } from "../http";

export type CreateScheduleBody =
  | { kind: "once"; run_at: string; timezone?: string; cron?: never }
  | { kind: "cron"; cron: string; timezone?: string; run_at?: never };
export interface ListSchedulesParams {
  limit?: number;
  cursor?: string;
}
export interface ListScheduleFiresParams {
  limit?: number;
  cursor?: string;
}
export interface PutScheduleDeliveryPolicyBody {
  route: string;
  enabled: boolean;
}

export class SchedulesResource {
  constructor(private readonly http: FridayHttpClient) {}
  create(taskId: string, input: CreateScheduleBody) {
    return this.http.requestJson<Schedule>({
      method: "POST",
      path: `/v1/tasks/${taskId}/schedules`,
      body: input,
      validate: validateSchedule,
    });
  }
  list(taskId: string, params: ListSchedulesParams = {}) {
    return this.http.requestJson<Page<Schedule>>({
      method: "GET",
      path: `/v1/tasks/${taskId}/schedules`,
      query: { limit: params.limit, cursor: params.cursor },
      validate: validateSchedulePage,
    });
  }
  get(taskId: string, scheduleId: string) {
    return this.http.requestJson<Schedule>({
      method: "GET",
      path: `/v1/tasks/${taskId}/schedules/${scheduleId}`,
      validate: validateSchedule,
    });
  }
  pause(taskId: string, scheduleId: string) {
    return this.control(taskId, scheduleId, "pause");
  }
  resume(taskId: string, scheduleId: string) {
    return this.control(taskId, scheduleId, "resume");
  }
  cancel(taskId: string, scheduleId: string) {
    return this.control(taskId, scheduleId, "cancel");
  }
  fires(
    taskId: string,
    scheduleId: string,
    params: ListScheduleFiresParams = {},
  ) {
    return this.http.requestJson<Page<ScheduleFire>>({
      method: "GET",
      path: `/v1/tasks/${taskId}/schedules/${scheduleId}/fires`,
      query: { limit: params.limit, cursor: params.cursor },
      validate: validateScheduleFirePage,
    });
  }
  deliveryPolicy(taskId: string, scheduleId: string) {
    return this.http.requestJson<ScheduleDeliveryPolicy>({
      method: "GET",
      path: `/v1/tasks/${taskId}/schedules/${scheduleId}/delivery-policy`,
      validate: validateScheduleDeliveryPolicy,
    });
  }
  putDeliveryPolicy(
    taskId: string,
    scheduleId: string,
    input: PutScheduleDeliveryPolicyBody,
  ) {
    return this.http.requestJson<ScheduleDeliveryPolicy>({
      method: "PUT",
      path: `/v1/tasks/${taskId}/schedules/${scheduleId}/delivery-policy`,
      body: input,
      validate: validateScheduleDeliveryPolicy,
    });
  }
  private control(
    taskId: string,
    scheduleId: string,
    action: "pause" | "resume" | "cancel",
  ) {
    return this.http.requestJson<Schedule>({
      method: "POST",
      path: `/v1/tasks/${taskId}/schedules/${scheduleId}/${action}`,
      validate: validateSchedule,
    });
  }
}
