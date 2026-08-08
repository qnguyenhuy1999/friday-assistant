import {
  validateAgent,
  validateAgentPage,
  validateAgentRevision,
  validateAgentRevisions,
} from "@friday/contracts";
import { describe, expect, it, vi } from "vitest";
import { FridayHttpClient } from "../http";
import { AgentsResource } from "./agents";

describe("AgentsResource", () => {
  it("maps create, list, get, revision, and lifecycle operations", async () => {
    const requestJson = vi.fn().mockResolvedValue({});
    const agents = new AgentsResource({
      requestJson,
    } as unknown as FridayHttpClient);

    await agents.create({
      key: "coder",
      display_name: "Coder",
      description: "desc",
    });
    await agents.list();
    await agents.get("a-1");
    await agents.createRevision("a-1", {
      instructions: "be helpful",
      runtime_kind: "claude_cli",
      runtime_config: {},
      source_kind: "operator",
    });
    await agents.listRevisions("a-1");
    await agents.activateRevision("a-1", "r-1");
    await agents.disable("a-1");
    await agents.archive("a-1");

    expect(requestJson).toHaveBeenNthCalledWith(1, {
      method: "POST",
      path: "/v1/agents",
      body: { key: "coder", display_name: "Coder", description: "desc" },
      validate: validateAgent,
    });
    expect(requestJson).toHaveBeenNthCalledWith(2, {
      method: "GET",
      path: "/v1/agents",
      validate: validateAgentPage,
    });
    expect(requestJson).toHaveBeenNthCalledWith(3, {
      method: "GET",
      path: "/v1/agents/a-1",
      validate: validateAgent,
    });
    expect(requestJson).toHaveBeenNthCalledWith(4, {
      method: "POST",
      path: "/v1/agents/a-1/revisions",
      body: {
        instructions: "be helpful",
        runtime_kind: "claude_cli",
        runtime_config: {},
        source_kind: "operator",
      },
      validate: validateAgentRevision,
    });
    expect(requestJson).toHaveBeenNthCalledWith(5, {
      method: "GET",
      path: "/v1/agents/a-1/revisions",
      validate: validateAgentRevisions,
    });
    expect(requestJson).toHaveBeenNthCalledWith(6, {
      method: "POST",
      path: "/v1/agents/a-1/revisions/r-1/activate",
      validate: validateAgent,
    });
    expect(requestJson).toHaveBeenNthCalledWith(7, {
      method: "POST",
      path: "/v1/agents/a-1/disable",
      validate: validateAgent,
    });
    expect(requestJson).toHaveBeenNthCalledWith(8, {
      method: "POST",
      path: "/v1/agents/a-1/archive",
      validate: validateAgent,
    });
  });

  it("uses contract validation for agent responses", async () => {
    const agents = new AgentsResource(
      new FridayHttpClient({
        baseUrl: "http://api.test",
        fetchImpl: vi
          .fn()
          .mockResolvedValue(
            new Response(JSON.stringify({ id: "missing-required-fields" })),
          ),
      }),
    );

    await expect(agents.get("a-1")).rejects.toThrow(/wire contract/);
  });
});
