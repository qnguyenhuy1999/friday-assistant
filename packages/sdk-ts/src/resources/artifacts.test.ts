import { describe, expect, it, vi } from "vitest";
import type { FridayHttpClient } from "../http";
import { ArtifactsResource } from "./artifacts";

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

describe("ArtifactsResource", () => {
  it("records an artifact with the body verbatim", async () => {
    const { http, request } = client();
    const body = {
      kind: "file" as const,
      name: "repo",
      media_type: "inode/directory",
      location: "/tmp/repo",
    };
    await new ArtifactsResource(http).record("r-1", body);
    expect(request).toHaveBeenCalledWith({
      method: "POST",
      path: "/v1/runs/r-1/artifacts",
      body,
    });
  });

  it("reads a single artifact and a run's artifact page", async () => {
    const { http, request } = client();
    const artifacts = new ArtifactsResource(http);
    await artifacts.get("a-1");
    await artifacts.listForRun("r-1", { cursor: "c" });
    expect(request).toHaveBeenNthCalledWith(1, {
      method: "GET",
      path: "/v1/artifacts/a-1",
    });
    expect(request).toHaveBeenNthCalledWith(2, {
      method: "GET",
      path: "/v1/runs/r-1/artifacts",
      query: { limit: undefined, cursor: "c" },
    });
  });
});
