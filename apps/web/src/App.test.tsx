import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Skill, Task, TaskSkillBinding } from "@friday/contracts";
import { App } from "./App";

function renderApp() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );
}

describe("App", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/");
    vi.spyOn(global, "fetch").mockImplementation(
      async () =>
        new Response(JSON.stringify({ items: [], next_cursor: null }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders the shell and conversation view by default", async () => {
    renderApp();
    expect(
      screen.getByRole("heading", { name: "Friday Agent OS" }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: "Conversation" }),
    ).toBeInTheDocument();
  });

  it("routes ?view=approvals&id=... to the run-scoped approvals view", async () => {
    window.history.replaceState({}, "", "/?view=approvals&id=r-1");
    renderApp();
    expect(
      await screen.findByRole("heading", { name: "Approvals" }),
    ).toBeInTheDocument();
  });

  it("navigates to the first-class Agents registry", async () => {
    renderApp();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Agents" }));
    expect(
      await screen.findByRole("heading", { name: "Agents" }),
    ).toBeInTheDocument();
    expect(window.location.search).toBe("?view=agents");
  });

  it("navigates to the first-class Workflows registry and exact detail route", async () => {
    renderApp();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Workflows" }));
    expect(
      await screen.findByRole("heading", { name: "Workflows" }),
    ).toBeInTheDocument();
    expect(window.location.search).toBe("?view=workflows");
  });

  it("navigates to the first-class Skills registry", async () => {
    renderApp();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Skills" }));
    expect(
      await screen.findByRole("heading", { name: "Skills" }),
    ).toBeInTheDocument();
    expect(window.location.search).toBe("?view=skills");
  });

  it("navigates from exact Skill usage evidence to its Run route", async () => {
    vi.restoreAllMocks();
    vi.spyOn(global, "fetch").mockImplementation(async (input) => {
      const pathname = new URL(String(input)).pathname;
      if (pathname === "/v1/skills/s-1")
        return new Response(
          JSON.stringify({
            id: "s-1",
            key: "review.security",
            display_name: "Security review",
            description: "",
            status: "active",
            active_revision_id: "sr-2",
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
          }),
        );
      if (pathname.endsWith("/revisions/sr-2"))
        return new Response(
          JSON.stringify({
            id: "sr-2",
            skill_id: "s-1",
            version: 2,
            instructions: "Review security boundaries.",
            content_sha256: "a".repeat(64),
            source_kind: "operator",
            created_at: "2026-01-01T00:00:00Z",
          }),
        );
      if (pathname.endsWith("/revisions"))
        return new Response(
          JSON.stringify([
            {
              id: "sr-2",
              skill_id: "s-1",
              version: 2,
              instructions: "Review security boundaries.",
              content_sha256: "a".repeat(64),
              source_kind: "operator",
              created_at: "2026-01-01T00:00:00Z",
            },
          ]),
        );
      if (pathname.endsWith("/usage"))
        return new Response(
          JSON.stringify([
            {
              id: "usage-1",
              run_id: "run-from-usage",
              task_id: "task-1",
              skill_id: "s-1",
              revision_id: "sr-1",
              position: 1,
              resolution_id: "resolution-1",
              execution_id: "execution-1",
              attempt_number: 1,
              started_at: null,
              outcome: "succeeded",
              failure_code: null,
              tool_call_count: 0,
              approval_count: 0,
              duration_ms: null,
              completed_at: "2026-01-01T00:00:00Z",
              created_at: "2026-01-01T00:00:00Z",
            },
          ]),
        );
      return new Response(JSON.stringify({ items: [], next_cursor: null }));
    });
    window.history.replaceState({}, "", "/?view=skill&id=s-1");
    renderApp();

    const evidence = await screen.findByRole("article", {
      name: "Usage evidence for Run run-from-usage",
    });
    await userEvent
      .setup()
      .click(within(evidence).getByRole("button", { name: "View Run" }));
    expect(window.location.search).toBe("?view=run&id=run-from-usage");
  });

  it("reads and navigates a Workflow detail route", async () => {
    window.history.replaceState({}, "", "/?view=workflow&id=w-1");
    renderApp();
    expect(
      await screen.findByText("Failed to load Workflow."),
    ).toBeInTheDocument();
  });

  it("reads an exact Task detail route", async () => {
    vi.restoreAllMocks();
    vi.spyOn(global, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/v1/tasks/t-1"))
        return new Response(
          JSON.stringify({
            id: "t-1",
            title: "Ship it",
            description: "A task to inspect.",
            status: "active",
            created_at: "2026-01-01T00:00:00Z",
            failure: null,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      if (
        url.endsWith("/v1/tasks/t-1/agent") ||
        url.endsWith("/v1/tasks/t-1/workflow")
      )
        return new Response("null", {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      return new Response(JSON.stringify({ items: [], next_cursor: null }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    window.history.replaceState({}, "", "/?view=task&id=t-1");
    renderApp();
    expect(
      await screen.findByRole("heading", { name: "Ship it" }),
    ).toBeInTheDocument();
    expect(window.location.search).toBe("?view=task&id=t-1");
  });

  it("isolates unsaved Skill drafts when the Task route changes", async () => {
    vi.restoreAllMocks();
    const taskA: Task = {
      id: "task-a",
      title: "Task A",
      description: "First task.",
      status: "active",
      created_at: "2026-01-01T00:00:00Z",
      failure: null,
    };
    const taskB: Task = {
      ...taskA,
      id: "task-b",
      title: "Task B",
      description: "Second task.",
    };
    const skills: Record<string, Skill> = {
      "skill-a": {
        id: "skill-a",
        key: "review.a",
        display_name: "Review A",
        description: "Reviews A.",
        status: "active",
        active_revision_id: "revision-a",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
      "skill-b": {
        id: "skill-b",
        key: "review.b",
        display_name: "Review B",
        description: "Reviews B.",
        status: "active",
        active_revision_id: "revision-b",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
      "skill-c": {
        id: "skill-c",
        key: "review.c",
        display_name: "Review C",
        description: "Reviews C.",
        status: "active",
        active_revision_id: "revision-c",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
      "skill-d": {
        id: "skill-d",
        key: "review.d",
        display_name: "Review D",
        description: "Reviews D.",
        status: "active",
        active_revision_id: "revision-d",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
    };
    const tasks: Record<string, Task> = {
      [taskA.id]: taskA,
      [taskB.id]: taskB,
    };
    const skillIdsByTask: Record<string, string[]> = {
      [taskA.id]: ["skill-a"],
      [taskB.id]: ["skill-c"],
    };
    const writes: Array<{
      taskId: string;
      path: string;
      body: unknown;
    }> = [];
    const binding = (taskId: string, skillId: string, position: number) =>
      ({
        task_id: taskId,
        skill_id: skillId,
        position,
        created_at: "2026-01-02T00:00:00Z",
      }) satisfies TaskSkillBinding;

    vi.spyOn(global, "fetch").mockImplementation(async (input, init) => {
      const url = new URL(String(input));
      const method = init?.method ?? "GET";
      const path = url.pathname;
      const taskMatch = path.match(/^\/v1\/tasks\/(task-a|task-b)$/);
      if (taskMatch) {
        const taskId = taskMatch[1];
        if (!taskId) throw new Error("Task route did not include an ID");
        return new Response(JSON.stringify(tasks[taskId]));
      }

      const bindingMatch = path.match(
        /^\/v1\/tasks\/(task-a|task-b)\/(agent|workflow)$/,
      );
      if (bindingMatch) return new Response("null");

      const skillBindingMatch = path.match(
        /^\/v1\/tasks\/(task-a|task-b)\/skills$/,
      );
      if (skillBindingMatch) {
        const taskId = skillBindingMatch[1];
        if (!taskId)
          throw new Error("Skill binding route did not include an ID");
        if (method === "PUT") {
          const body = JSON.parse(String(init?.body)) as {
            skill_ids: string[];
          };
          writes.push({ taskId, path, body });
          skillIdsByTask[taskId] = body.skill_ids;
        }
        const skillIds = skillIdsByTask[taskId];
        if (!skillIds) throw new Error(`No Skill composition for ${taskId}`);
        return new Response(
          JSON.stringify(
            skillIds.map((skillId, index) =>
              binding(taskId, skillId, index + 1),
            ),
          ),
        );
      }

      if (path === "/v1/agents" || path === "/v1/workflows")
        return new Response(JSON.stringify({ items: [], next_cursor: null }));
      if (path === "/v1/skills")
        return new Response(
          JSON.stringify({ items: Object.values(skills), next_cursor: null }),
        );
      const exactSkillMatch = path.match(/^\/v1\/skills\/(.+)$/);
      if (exactSkillMatch) {
        const skillId = exactSkillMatch[1];
        if (!skillId) throw new Error("Skill route did not include an ID");
        return new Response(JSON.stringify(skills[skillId]));
      }
      return new Response(JSON.stringify({ items: [], next_cursor: null }));
    });

    window.history.replaceState({}, "", "/?view=task&id=task-a");
    renderApp();
    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "Task A" });
    await user.selectOptions(screen.getByLabelText("Skill"), "skill-b");
    await user.click(
      screen.getByRole("button", { name: "Add selected Skill" }),
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "Skill composition has unsaved changes",
    );
    expect(
      within(
        screen.getByRole("list", { name: "Task Skill composition" }),
      ).getAllByRole("listitem"),
    ).toHaveLength(2);
    expect(writes).toHaveLength(0);

    window.history.pushState({}, "", "/?view=task&id=task-b");
    window.dispatchEvent(new PopStateEvent("popstate"));
    await screen.findByRole("heading", { name: "Task B" });
    const taskBComposition = await screen.findByRole("list", {
      name: "Task Skill composition",
    });
    expect(within(taskBComposition).getAllByRole("listitem")).toHaveLength(1);
    expect(
      within(taskBComposition).getByRole("heading", { name: /Review C/ }),
    ).toBeInTheDocument();
    expect(
      within(taskBComposition).queryByText("Review A"),
    ).not.toBeInTheDocument();
    expect(
      within(taskBComposition).queryByText("Review B"),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(writes).toHaveLength(0);

    await user.selectOptions(screen.getByLabelText("Skill"), "skill-d");
    await user.click(
      screen.getByRole("button", { name: "Add selected Skill" }),
    );
    await user.click(
      screen.getByRole("button", { name: "Save Skill composition" }),
    );
    await waitFor(() => {
      expect(writes).toEqual([
        {
          taskId: taskB.id,
          path: "/v1/tasks/task-b/skills",
          body: { skill_ids: ["skill-c", "skill-d"] },
        },
      ]);
    });
    expect(writes.some((write) => write.taskId === taskA.id)).toBe(false);
  });

  it("navigates back from Workflow detail to the registry", async () => {
    vi.restoreAllMocks();
    vi.spyOn(global, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/workflows/w-1"))
        return new Response(
          JSON.stringify({
            id: "w-1",
            key: "release.pipeline",
            display_name: "Release pipeline",
            description: "",
            status: "active",
            active_revision_id: null,
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      if (url.includes("/revisions?"))
        return new Response("[]", {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      return new Response(JSON.stringify({ items: [], next_cursor: null }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    window.history.replaceState({}, "", "/?view=workflow&id=w-1");
    renderApp();
    await screen.findByRole("heading", { name: "Release pipeline" });
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Back to Workflows" }));
    expect(
      await screen.findByRole("heading", { name: "Workflows" }),
    ).toBeInTheDocument();
    expect(window.location.search).toBe("?view=workflows");
  });

  it("renders no detail view when the route carries no id", () => {
    window.history.replaceState({}, "", "/?view=run");
    renderApp();
    expect(screen.queryByText(/^Status:/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Approvals" }),
    ).not.toBeInTheDocument();
  });
});
