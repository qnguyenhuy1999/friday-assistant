import {
  validateSchedule,
  validateScheduleFirePage,
  validateSchedulePage,
} from "@friday/contracts";
import { describe, expect, it, vi } from "vitest";
import { FridayHttpClient } from "../http";
import { SchedulesResource } from "./schedules";

describe("SchedulesResource", () => {
  it("maps create, list, get, control, and fire operations", async () => {
    const requestJson = vi.fn().mockResolvedValue({});
    const schedules = new SchedulesResource({
      requestJson,
    } as unknown as FridayHttpClient);

    await schedules.create("t-1", {
      kind: "cron",
      cron: "0 9 * * *",
      timezone: "UTC",
    });
    await schedules.list("t-1", { limit: 10, cursor: "next" });
    await schedules.get("t-1", "s-1");
    await schedules.pause("t-1", "s-1");
    await schedules.resume("t-1", "s-1");
    await schedules.cancel("t-1", "s-1");
    await schedules.fires("t-1", "s-1", { limit: 5, cursor: "fires" });

    expect(requestJson).toHaveBeenNthCalledWith(1, {
      method: "POST",
      path: "/v1/tasks/t-1/schedules",
      body: { kind: "cron", cron: "0 9 * * *", timezone: "UTC" },
      validate: validateSchedule,
    });
    expect(requestJson).toHaveBeenNthCalledWith(2, {
      method: "GET",
      path: "/v1/tasks/t-1/schedules",
      query: { limit: 10, cursor: "next" },
      validate: validateSchedulePage,
    });
    expect(requestJson).toHaveBeenNthCalledWith(3, {
      method: "GET",
      path: "/v1/tasks/t-1/schedules/s-1",
      validate: validateSchedule,
    });
    for (const [index, action] of ["pause", "resume", "cancel"].entries()) {
      expect(requestJson).toHaveBeenNthCalledWith(index + 4, {
        method: "POST",
        path: `/v1/tasks/t-1/schedules/s-1/${action}`,
        validate: validateSchedule,
      });
    }
    expect(requestJson).toHaveBeenNthCalledWith(7, {
      method: "GET",
      path: "/v1/tasks/t-1/schedules/s-1/fires",
      query: { limit: 5, cursor: "fires" },
      validate: validateScheduleFirePage,
    });
  });

  it("uses contract validation for schedule responses", async () => {
    const schedules = new SchedulesResource(
      new FridayHttpClient({
        baseUrl: "http://api.test",
        fetchImpl: vi
          .fn()
          .mockResolvedValue(
            new Response(JSON.stringify({ id: "missing-required-fields" })),
          ),
      }),
    );

    await expect(schedules.get("t-1", "s-1")).rejects.toThrow(/wire contract/);
  });
});
