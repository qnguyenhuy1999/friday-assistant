import { describe, expect, it, vi } from "vitest";
import {
  WireFormatError,
  validateRun,
  validateRunPage,
} from "@friday/contracts";
import { FridayHttpClient } from "../http";
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
  return {
    http: { requestJson: request } as unknown as FridayHttpClient,
    request,
  };
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
      validate: validateRun,
    });
    expect(request).toHaveBeenNthCalledWith(2, {
      method: "GET",
      path: "/v1/tasks/t-1/runs",
      query: { limit: 5, cursor: "c" },
      validate: validateRunPage,
    });
  });

  it("validates a task's run page through the real HTTP client", async () => {
    const response = (body: unknown) => new Response(JSON.stringify(body));
    const runs = new RunsResource(
      new FridayHttpClient({
        baseUrl: "http://api.test",
        fetchImpl: vi.fn().mockResolvedValue(
          response({
            items: [
              {
                id: "r-1",
                task_id: "t-1",
                status: "queued",
                created_at: "now",
                failure: null,
              },
            ],
            next_cursor: null,
          }),
        ),
      }),
    );
    await expect(runs.listForTask("t-1")).resolves.toMatchObject({
      items: [{ task_id: "t-1" }],
    });
    const invalid = new RunsResource(
      new FridayHttpClient({
        baseUrl: "http://api.test",
        fetchImpl: vi
          .fn()
          .mockResolvedValue(
            response({ items: [{ id: "r-1" }], next_cursor: null }),
          ),
      }),
    );
    await expect(invalid.listForTask("t-1")).rejects.toBeInstanceOf(
      WireFormatError,
    );
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
      validate: validateRun,
    });
    expect(request).toHaveBeenNthCalledWith(2, {
      method: "POST",
      path: "/v1/runs/r-1/complete",
      validate: validateRun,
    });
    expect(request).toHaveBeenNthCalledWith(3, {
      method: "POST",
      path: "/v1/runs/r-1/cancel",
      validate: validateRun,
    });
    expect(request).toHaveBeenNthCalledWith(4, {
      method: "POST",
      path: "/v1/runs/r-1/retry",
      validate: validateRun,
    });
    expect(request).toHaveBeenNthCalledWith(5, {
      method: "POST",
      path: "/v1/runs/r-1/fail",
      body: failure,
      validate: validateRun,
    });
  });
});
