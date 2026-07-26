import { describe, expect, it, vi } from "vitest";
import type { FridayHttpClient } from "../http";
import { ApprovalsResource } from "./approvals";

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

describe("ApprovalsResource", () => {
  it("forwards the request body verbatim to /v1/runs/{id}/approvals", async () => {
    const { http, request } = client();
    const body = {
      category: "computer_use" as const,
      summary: "Click Send",
      reason: "explicit sign-off",
      requested_action: "computer.click",
      requested_input: { pid: 1 },
    };
    await new ApprovalsResource(http).request("r-1", body);
    expect(request).toHaveBeenCalledWith({
      method: "POST",
      path: "/v1/runs/r-1/approvals",
      body,
    });
  });

  it("reads a single approval and a run's approval page", async () => {
    const { http, request } = client();
    const approvals = new ApprovalsResource(http);
    await approvals.get("a-1");
    await approvals.listForRun("r-1", { limit: 25 });
    expect(request).toHaveBeenNthCalledWith(1, {
      method: "GET",
      path: "/v1/approvals/a-1",
    });
    expect(request).toHaveBeenNthCalledWith(2, {
      method: "GET",
      path: "/v1/runs/r-1/approvals",
      query: { limit: 25, cursor: undefined },
    });
  });

  it("posts each resolution to its own endpoint", async () => {
    const { http, request } = client();
    const approvals = new ApprovalsResource(http);
    await approvals.approve("a-1", {
      resolver: "patrick",
      resolution_note: "ok",
    });
    await approvals.reject("a-1", { resolver: "patrick" });
    await approvals.cancel("a-1", { resolution_note: "task cancelled" });
    await approvals.expire("a-1");
    expect(request).toHaveBeenNthCalledWith(1, {
      method: "POST",
      path: "/v1/approvals/a-1/approve",
      body: { resolver: "patrick", resolution_note: "ok" },
    });
    expect(request).toHaveBeenNthCalledWith(2, {
      method: "POST",
      path: "/v1/approvals/a-1/reject",
      body: { resolver: "patrick" },
    });
    expect(request).toHaveBeenNthCalledWith(3, {
      method: "POST",
      path: "/v1/approvals/a-1/cancel",
      body: { resolution_note: "task cancelled" },
    });
    expect(request).toHaveBeenNthCalledWith(4, {
      method: "POST",
      path: "/v1/approvals/a-1/expire",
    });
  });

  it("never adds, drops, renames, or mutates a key on the caller's resolution body", async () => {
    const { http, request } = client();
    const body = { resolver: "patrick", resolution_note: "note" };
    const original = { ...body };
    await new ApprovalsResource(http).approve("a-1", body);
    expect(body).toEqual(original);
    const [sent] = request.mock.calls[0] as [{ body: object }];
    expect(Object.keys(sent.body).sort()).toEqual([
      "resolution_note",
      "resolver",
    ]);
  });

  it("never resolves an approval as a side effect of reading it", async () => {
    const { http, request } = client();
    const approvals = new ApprovalsResource(http);
    await approvals.get("a-1");
    await approvals.listForRun("r-1");
    for (const [call] of request.mock.calls as [{ method: string }][]) {
      expect(call.method).toBe("GET");
    }
  });
});
