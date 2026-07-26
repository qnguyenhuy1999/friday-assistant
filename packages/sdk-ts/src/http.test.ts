import { describe, expect, it, vi } from "vitest";
import { FridayApiError, FridayHttpClient, FridayNetworkError } from "./http";
import { WireFormatError, validateTask } from "@friday/contracts";

const response = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
describe("FridayHttpClient", () => {
  it("builds URLs, omits undefined query values, and serializes bodies", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(response({ ok: true }));
    const client = new FridayHttpClient({
      baseUrl: "http://api.test/",
      fetchImpl,
    });
    await client.request({
      method: "POST",
      path: "/test/echo",
      query: { limit: 25, cursor: undefined },
      body: { title: "x" },
    });
    expect(fetchImpl).toHaveBeenCalledWith(
      "http://api.test/test/echo?limit=25",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ title: "x" }),
        headers: { "Content-Type": "application/json" },
      }),
    );
  });
  it("maps API envelopes to a typed API error", async () => {
    const client = new FridayHttpClient({
      baseUrl: "http://api.test",
      fetchImpl: vi.fn().mockResolvedValue(
        response(
          {
            error: { type: "run_not_found", message: "no run", details: {} },
          },
          404,
        ),
      ),
    });
    await expect(
      client.request({ method: "GET", path: "/v1/runs/nope" }),
    ).rejects.toMatchObject({
      name: "FridayApiError",
      status: 404,
      errorType: "run_not_found",
    } satisfies Partial<FridayApiError>);
  });
  it("rejects a successful response that drifts from the wire contract", async () => {
    const client = new FridayHttpClient({
      baseUrl: "http://api.test",
      fetchImpl: vi.fn().mockResolvedValue(response({ id: "t-1" })),
    });
    await expect(
      client.request({
        method: "GET",
        path: "/v1/tasks/t-1",
        validate: validateTask,
      }),
    ).rejects.toBeInstanceOf(WireFormatError);
  });
  it("wraps transport failures but preserves caller cancellations", async () => {
    const client = new FridayHttpClient({
      baseUrl: "http://api.test",
      fetchImpl: vi.fn().mockRejectedValue(new TypeError("offline")),
    });
    await expect(
      client.request({ method: "GET", path: "/v1/tasks" }),
    ).rejects.toBeInstanceOf(FridayNetworkError);
    const controller = new AbortController();
    controller.abort();
    const cancelled = new FridayHttpClient({
      baseUrl: "http://api.test",
      fetchImpl: vi
        .fn()
        .mockRejectedValue(new DOMException("aborted", "AbortError")),
    });
    await expect(
      cancelled.request({
        method: "GET",
        path: "/v1/tasks",
        signal: controller.signal,
      }),
    ).rejects.toMatchObject({ name: "AbortError" });
  });
  it("aborts and reports a network error once defaultTimeoutMs elapses", async () => {
    const client = new FridayHttpClient({
      baseUrl: "http://api.test",
      defaultTimeoutMs: 5,
      fetchImpl: (_url, init) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    });
    await expect(
      client.request({ method: "GET", path: "/v1/tasks" }),
    ).rejects.toBeInstanceOf(FridayNetworkError);
  });
  it("returns undefined for a 204 instead of parsing an empty body", async () => {
    const client = new FridayHttpClient({
      baseUrl: "http://api.test",
      fetchImpl: vi.fn().mockResolvedValue(new Response(null, { status: 204 })),
    });
    await expect(
      client.request({ method: "POST", path: "/v1/runs/r-1/cancel" }),
    ).resolves.toBeUndefined();
  });
  it("falls back to the status text when an error body is not JSON", async () => {
    const client = new FridayHttpClient({
      baseUrl: "http://api.test",
      fetchImpl: vi.fn().mockResolvedValue(
        new Response("<html>gateway</html>", {
          status: 502,
          statusText: "Bad Gateway",
        }),
      ),
    });
    await expect(
      client.request({ method: "GET", path: "/v1/tasks" }),
    ).rejects.toMatchObject({
      name: "FridayApiError",
      status: 502,
      errorType: "unknown_error",
      message: "Bad Gateway",
    });
  });
});
