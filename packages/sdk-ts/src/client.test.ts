import { describe, expect, it, vi } from "vitest";
import { FridayClient } from "./client";
import {
  AgentsResource,
  ApprovalsResource,
  ArtifactsResource,
  DelegationsResource,
  EventsResource,
  HealthResource,
  RunsResource,
  SkillsResource,
  StepsResource,
  TasksResource,
  ToolInvocationsResource,
} from "./index";

describe("FridayClient", () => {
  it("composes every resource", () => {
    const friday = new FridayClient({ baseUrl: "http://localhost:8000" });
    expect(friday.tasks).toBeInstanceOf(TasksResource);
    expect(friday.runs).toBeInstanceOf(RunsResource);
    expect(friday.steps).toBeInstanceOf(StepsResource);
    expect(friday.approvals).toBeInstanceOf(ApprovalsResource);
    expect(friday.toolInvocations).toBeInstanceOf(ToolInvocationsResource);
    expect(friday.artifacts).toBeInstanceOf(ArtifactsResource);
    expect(friday.events).toBeInstanceOf(EventsResource);
    expect(friday.health).toBeInstanceOf(HealthResource);
    expect(friday.skills).toBeInstanceOf(SkillsResource);
    expect(friday.agents).toBeInstanceOf(AgentsResource);
    expect(friday.delegations).toBeInstanceOf(DelegationsResource);
  });

  it("routes every resource through the one configured base URL", async () => {
    // A Response body can only be read once, so hand out a fresh one per call.
    const fetchImpl = vi.fn<(url: RequestInfo | URL) => Promise<Response>>(
      async () =>
        new Response(
          JSON.stringify({
            id: "t-1",
            title: "test",
            description: "",
            status: "pending",
            created_at: "2026-01-01T00:00:00Z",
            failure: null,
          }),
          {
            status: 200,
            headers: { "content-type": "application/json" },
          },
        ),
    );
    const friday = new FridayClient({
      baseUrl: "http://api.test/",
      fetchImpl,
    });
    await friday.tasks.get("t-1");
    await friday.health.get();
    expect(fetchImpl.mock.calls.map(([url]) => url)).toEqual([
      "http://api.test/v1/tasks/t-1",
      "http://api.test/health",
    ]);
  });
});
