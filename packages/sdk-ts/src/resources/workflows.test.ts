import { validateWorkflow, validateWorkflowPage } from "@friday/contracts";
import { describe, expect, it, vi } from "vitest";
import { FridayHttpClient } from "../http";
import { WorkflowsResource } from "./workflows";

describe("WorkflowsResource", () => {
  it("forwards workflow list pagination parameters", async () => {
    const requestJson = vi.fn().mockResolvedValue({});
    const workflows = new WorkflowsResource({
      requestJson,
    } as unknown as FridayHttpClient);

    await workflows.list({ limit: 10, cursor: "next" });

    expect(requestJson).toHaveBeenCalledWith({
      method: "GET",
      path: "/v1/workflows",
      query: { limit: 10, cursor: "next" },
      validate: validateWorkflowPage,
    });
  });

  it("keeps response validation on workflow operations", async () => {
    const requestJson = vi.fn().mockResolvedValue({});
    const workflows = new WorkflowsResource({
      requestJson,
    } as unknown as FridayHttpClient);

    await workflows.get("w-1");

    expect(requestJson).toHaveBeenCalledWith({
      method: "GET",
      path: "/v1/workflows/w-1",
      validate: validateWorkflow,
    });
  });
});
