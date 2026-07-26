import { describe, expect, it, vi } from "vitest";
import type { FridayHttpClient } from "../http";
import { RunsResource } from "./runs";

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

describe("RunsResource", () => {
  it("reads a run and a task's run page", async () => {
    const { http, request } = client();
    const runs = new RunsResource(http);
    await runs.get("r-1");
    await runs.listForTask("t-1", { limit: 5, cursor: "c" });
    expect(request).toHaveBeenNthCalledWith(1, {
      method: "GET",
      path: "/v1/runs/r-1",
    });
    expect(request).toHaveBeenNthCalledWith(2, {
      method: "GET",
      path: "/v1/tasks/t-1/runs",
      query: { limit: 5, cursor: "c" },
    });
  });

  it("posts each lifecycle transition to its own endpoint", async () => {
    const { http, request } = client();
    const runs = new RunsResource(http);
    await runs.start("r-1");
    await runs.complete("r-1");
    await runs.cancel("r-1");
    await runs.retry("r-1");
    await runs.fail("r-1", failure);
    expect(request).toHaveBeenNthCalledWith(1, {
      method: "POST",
      path: "/v1/runs/r-1/start",
    });
    expect(request).toHaveBeenNthCalledWith(2, {
      method: "POST",
      path: "/v1/runs/r-1/complete",
    });
    expect(request).toHaveBeenNthCalledWith(3, {
      method: "POST",
      path: "/v1/runs/r-1/cancel",
    });
    expect(request).toHaveBeenNthCalledWith(4, {
      method: "POST",
      path: "/v1/runs/r-1/retry",
    });
    expect(request).toHaveBeenNthCalledWith(5, {
      method: "POST",
      path: "/v1/runs/r-1/fail",
      body: failure,
    });
  });
});
