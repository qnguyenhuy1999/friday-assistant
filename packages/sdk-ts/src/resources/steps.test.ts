import { describe, expect, it, vi } from "vitest";
import type { FridayHttpClient } from "../http";
import { StepsResource } from "./steps";

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
    http: {
      requestJson: ({
        validate,
        ...options
      }: {
        validate: unknown;
        [key: string]: unknown;
      }) => (void validate, request(options)),
    } as unknown as FridayHttpClient,
    request,
  };
}

describe("StepsResource", () => {
  it("creates and reads steps", async () => {
    const { http, request } = client();
    const steps = new StepsResource(http);
    await steps.create("r-1", { name: "clone" });
    await steps.listForRun("r-1", { limit: 25 });
    await steps.get("s-1");
    expect(request).toHaveBeenNthCalledWith(1, {
      method: "POST",
      path: "/v1/runs/r-1/steps",
      body: { name: "clone" },
    });
    expect(request).toHaveBeenNthCalledWith(2, {
      method: "GET",
      path: "/v1/runs/r-1/steps",
      query: { limit: 25, cursor: undefined },
    });
    expect(request).toHaveBeenNthCalledWith(3, {
      method: "GET",
      path: "/v1/steps/s-1",
    });
  });

  it("posts each lifecycle transition to its own endpoint", async () => {
    const { http, request } = client();
    const steps = new StepsResource(http);
    await steps.start("s-1");
    await steps.complete("s-1");
    await steps.skip("s-1");
    await steps.cancel("s-1");
    await steps.fail("s-1", failure);
    expect(request).toHaveBeenNthCalledWith(1, {
      method: "POST",
      path: "/v1/steps/s-1/start",
    });
    expect(request).toHaveBeenNthCalledWith(2, {
      method: "POST",
      path: "/v1/steps/s-1/complete",
    });
    expect(request).toHaveBeenNthCalledWith(3, {
      method: "POST",
      path: "/v1/steps/s-1/skip",
    });
    expect(request).toHaveBeenNthCalledWith(4, {
      method: "POST",
      path: "/v1/steps/s-1/cancel",
    });
    expect(request).toHaveBeenNthCalledWith(5, {
      method: "POST",
      path: "/v1/steps/s-1/fail",
      body: failure,
    });
  });
});
