import { describe, expect, it, vi } from "vitest";
import type { FridayHttpClient } from "../http";
import { TasksResource } from "./tasks";

const failure = {
  code: "x",
  message: "m",
  retryable: false,
  cause: "internal" as const,
  details: null,
};

function client() {
  const request = vi.fn().mockResolvedValue({});
  return { http: { request } as unknown as FridayHttpClient, request };
}

describe("TasksResource", () => {
  it("creates, lists, and reads tasks", async () => {
    const { http, request } = client();
    const tasks = new TasksResource(http);
    await tasks.create({ title: "Ship it" });
    await tasks.list({ limit: 10, cursor: "abc" });
    await tasks.get("t-1");
    expect(request).toHaveBeenNthCalledWith(1, {
      method: "POST",
      path: "/v1/tasks",
      body: { title: "Ship it" },
    });
    expect(request).toHaveBeenNthCalledWith(2, {
      method: "GET",
      path: "/v1/tasks",
      query: { limit: 10, cursor: "abc" },
    });
    expect(request).toHaveBeenNthCalledWith(3, {
      method: "GET",
      path: "/v1/tasks/t-1",
    });
  });

  it("posts each lifecycle transition to its own endpoint", async () => {
    const { http, request } = client();
    const tasks = new TasksResource(http);
    await tasks.startRun("t-1");
    await tasks.cancel("t-1");
    await tasks.complete("t-1");
    await tasks.fail("t-1", failure);
    expect(request).toHaveBeenNthCalledWith(1, {
      method: "POST",
      path: "/v1/tasks/t-1/runs",
    });
    expect(request).toHaveBeenNthCalledWith(2, {
      method: "POST",
      path: "/v1/tasks/t-1/cancel",
    });
    expect(request).toHaveBeenNthCalledWith(3, {
      method: "POST",
      path: "/v1/tasks/t-1/complete",
    });
    expect(request).toHaveBeenNthCalledWith(4, {
      method: "POST",
      path: "/v1/tasks/t-1/fail",
      body: failure,
    });
  });
});
