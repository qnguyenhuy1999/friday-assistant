import { validateDelegationRequest } from "@friday/contracts";
import { describe, expect, it, vi } from "vitest";
import { FridayHttpClient } from "../http";
import { DelegationsResource } from "./delegations";

describe("DelegationsResource", () => {
  it("maps get", async () => {
    const requestJson = vi.fn().mockResolvedValue({});
    const delegations = new DelegationsResource({
      requestJson,
    } as unknown as FridayHttpClient);

    await delegations.get("d-1");

    expect(requestJson).toHaveBeenNthCalledWith(1, {
      method: "GET",
      path: "/v1/delegations/d-1",
      validate: validateDelegationRequest,
    });
  });

  it("uses contract validation for delegation responses", async () => {
    const delegations = new DelegationsResource(
      new FridayHttpClient({
        baseUrl: "http://api.test",
        fetchImpl: vi
          .fn()
          .mockResolvedValue(
            new Response(JSON.stringify({ id: "missing-required-fields" })),
          ),
      }),
    );

    await expect(delegations.get("d-1")).rejects.toThrow(/wire contract/);
  });
});
