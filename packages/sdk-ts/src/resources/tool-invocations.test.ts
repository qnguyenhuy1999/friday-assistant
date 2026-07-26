import { describe, expect, it, vi } from "vitest";
import type { FridayHttpClient } from "../http";
import { ToolInvocationsResource } from "./tool-invocations";

const failure = {
  code: "x",
  message: "m",
  retryable: false,
  cause: "tool" as const,
  details: null,
};

function client() {
  const request = vi.fn().mockResolvedValue({});
  return {
    http: {
      requestJson: ({ validate, ...options }) => (
        void validate,
        request(options)
      ),
    } as unknown as FridayHttpClient,
    request,
  };
}

describe("ToolInvocationsResource", () => {
  it("requests an invocation and reads it by run and by step", async () => {
    const { http, request } = client();
    const invocations = new ToolInvocationsResource(http);
    await invocations.request("r-1", { tool_name: "shell.run" });
    await invocations.get("i-1");
    await invocations.listForRun("r-1", { limit: 10 });
    await invocations.listForStep("s-1");
    expect(request).toHaveBeenNthCalledWith(1, {
      method: "POST",
      path: "/v1/runs/r-1/tool-invocations",
      body: { tool_name: "shell.run" },
    });
    expect(request).toHaveBeenNthCalledWith(2, {
      method: "GET",
      path: "/v1/tool-invocations/i-1",
    });
    expect(request).toHaveBeenNthCalledWith(3, {
      method: "GET",
      path: "/v1/runs/r-1/tool-invocations",
      query: { limit: 10, cursor: undefined },
    });
    expect(request).toHaveBeenNthCalledWith(4, {
      method: "GET",
      path: "/v1/steps/s-1/tool-invocations",
      query: { limit: undefined, cursor: undefined },
    });
  });

  it("posts each lifecycle transition to its own endpoint", async () => {
    const { http, request } = client();
    const invocations = new ToolInvocationsResource(http);
    await invocations.markRunning("i-1");
    await invocations.markSucceeded("i-1", { output: { ok: true } });
    await invocations.markSucceeded("i-1");
    await invocations.markFailed("i-1", { failure });
    await invocations.cancel("i-1");
    expect(request).toHaveBeenNthCalledWith(1, {
      method: "POST",
      path: "/v1/tool-invocations/i-1/running",
    });
    expect(request).toHaveBeenNthCalledWith(2, {
      method: "POST",
      path: "/v1/tool-invocations/i-1/succeed",
      body: { output: { ok: true } },
    });
    expect(request).toHaveBeenNthCalledWith(3, {
      method: "POST",
      path: "/v1/tool-invocations/i-1/succeed",
      body: {},
    });
    expect(request).toHaveBeenNthCalledWith(4, {
      method: "POST",
      path: "/v1/tool-invocations/i-1/fail",
      body: { failure },
    });
    expect(request).toHaveBeenNthCalledWith(5, {
      method: "POST",
      path: "/v1/tool-invocations/i-1/cancel",
    });
  });
});
