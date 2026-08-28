import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Agent, Task, Workflow } from "@friday/contracts";
import { TaskDetailPage } from "./task-detail-page";

function response(body: unknown, status = 200): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const task: Task = {
  id: "t-1",
  title: "Ship it",
  description: "Coordinate the release.",
  status: "active",
  created_at: "2026-01-01T00:00:00Z",
  failure: null,
};

const activeAgent: Agent = {
  id: "a-active",
  key: "coding.agent",
  display_name: "Coding Agent",
  description: "Writes code safely.",
  status: "active",
  active_revision_id: "ar-1",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};
const disabledAgent: Agent = {
  ...activeAgent,
  id: "a-disabled",
  status: "disabled",
};
const archivedAgent: Agent = {
  ...activeAgent,
  id: "a-archived",
  status: "archived",
};
const unselectedAgent: Agent = {
  ...activeAgent,
  id: "a-unselected",
  active_revision_id: null,
};

const activeWorkflow: Workflow = {
  id: "w-active",
  key: "release.pipeline",
  display_name: "Release Pipeline",
  description: "Coordinates a release.",
  status: "active",
  active_revision_id: "wr-1",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};
const disabledWorkflow: Workflow = {
  ...activeWorkflow,
  id: "w-disabled",
  status: "disabled",
};
const unselectedWorkflow: Workflow = {
  ...activeWorkflow,
  id: "w-unselected",
  active_revision_id: null,
};
const archivedWorkflow: Workflow = {
  ...activeWorkflow,
  id: "w-archived",
  status: "archived",
};

type AgentBinding = {
  task_id: string;
  agent_id: string;
  created_at: string;
};
type WorkflowBinding = {
  task_id: string;
  workflow_id: string;
  created_at: string;
  updated_at: string;
};

function page(items: unknown[]) {
  return { items, next_cursor: null };
}

function installApi({
  taskValue = task,
  agentBinding: initialAgentBinding = null,
  workflowBinding: initialWorkflowBinding = null,
  agentItems = [activeAgent],
  workflowItems = [activeWorkflow],
  taskLoadFails = false,
  bindingLoadFails = false,
  agentMutationFails = false,
  startRunFails = false,
}: {
  taskValue?: Task;
  agentBinding?: AgentBinding | null;
  workflowBinding?: WorkflowBinding | null;
  agentItems?: Agent[];
  workflowItems?: Workflow[];
  taskLoadFails?: boolean;
  bindingLoadFails?: boolean;
  agentMutationFails?: boolean;
  startRunFails?: boolean;
} = {}) {
  let agentBinding = initialAgentBinding;
  let workflowBinding = initialWorkflowBinding;
  const fetchMock = vi
    .spyOn(global, "fetch")
    .mockImplementation(async (input, init) => {
      const url = new URL(String(input));
      const method = init?.method ?? "GET";
      const path = url.pathname;

      if (path === `/v1/tasks/${task.id}`)
        return taskLoadFails
          ? response(
              {
                error: {
                  type: "task_not_found",
                  message: "hidden",
                  details: {},
                },
              },
              404,
            )
          : response(taskValue);
      if (path === `/v1/tasks/${task.id}/agent`) {
        if (bindingLoadFails && method === "GET")
          return response(
            {
              error: { type: "internal_error", message: "hidden", details: {} },
            },
            500,
          );
        if (method === "PUT") {
          if (agentMutationFails)
            return response(
              {
                error: {
                  type: "entity_conflict",
                  message: "hidden",
                  details: {},
                },
              },
              409,
            );
          const body = JSON.parse(String(init?.body)) as {
            agent_id: string | null;
          };
          agentBinding = body.agent_id
            ? {
                task_id: task.id,
                agent_id: body.agent_id,
                created_at: "2026-01-02T00:00:00Z",
              }
            : null;
        }
        return response(agentBinding);
      }
      if (path === `/v1/tasks/${task.id}/workflow`) {
        if (method === "PUT") {
          const body = JSON.parse(String(init?.body)) as {
            workflow_id: string;
          };
          workflowBinding = {
            task_id: task.id,
            workflow_id: body.workflow_id,
            created_at: "2026-01-02T00:00:00Z",
            updated_at: "2026-01-02T00:00:00Z",
          };
        } else if (method === "DELETE") {
          workflowBinding = null;
          return response(null, 204);
        }
        return response(workflowBinding);
      }
      if (path === `/v1/tasks/${task.id}/runs`) {
        return startRunFails
          ? response(
              {
                error: {
                  type: "entity_conflict",
                  message: "hidden",
                  details: {},
                },
              },
              409,
            )
          : response({ task_id: task.id, run_id: "r-1" }, 201);
      }
      if (path === "/v1/agents") return response(page(agentItems));
      if (path === "/v1/workflows") return response(page(workflowItems));
      if (path.startsWith("/v1/agents/")) {
        const id = path.split("/").at(-1);
        const agent = agentItems.find((item) => item.id === id);
        return agent ? response(agent) : response({ error: {} }, 404);
      }
      if (path.startsWith("/v1/workflows/")) {
        const id = path.split("/").at(-1);
        const workflow = workflowItems.find((item) => item.id === id);
        return workflow ? response(workflow) : response({ error: {} }, 404);
      }
      return response(page([]));
    });
  return fetchMock;
}

function renderPage() {
  const onBack = vi.fn();
  const onRunStarted = vi.fn();
  const onViewSchedules = vi.fn();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <TaskDetailPage
        taskId="t-1"
        onBack={onBack}
        onRunStarted={onRunStarted}
        onViewSchedules={onViewSchedules}
      />
    </QueryClientProvider>,
  );
  return { onBack, onRunStarted, onViewSchedules };
}

describe("TaskDetailPage", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows task metadata, failure information, and Default Friday runtime", async () => {
    installApi({
      taskValue: {
        ...task,
        failure: {
          code: "runtime_failed",
          message: "The worker stopped.",
          retryable: false,
          cause: "runtime",
          details: { attempt: 1 },
        },
      },
    });
    renderPage();
    expect(
      await screen.findByRole("heading", { name: "Ship it" }),
    ).toBeInTheDocument();
    expect(screen.getByText("t-1")).toBeInTheDocument();
    expect(screen.getByText("Coordinate the release.")).toBeInTheDocument();
    expect(screen.getByText("runtime_failed")).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: "Default Friday runtime" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Friday's existing default processing behavior applies/),
    ).toBeInTheDocument();
  });

  it("binds an eligible Agent and shows its mutable execution preview", async () => {
    installApi();
    renderPage();
    const user = userEvent.setup();
    await screen.findByRole("option", { name: /Coding Agent/ });
    await user.selectOptions(screen.getByLabelText("Agent target"), "a-active");
    await user.click(
      screen.getByRole("button", { name: "Bind selected Agent" }),
    );
    expect(
      await screen.findByRole("heading", { name: "Agent" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Coding Agent")).toBeInTheDocument();
    expect(screen.getByText("coding.agent")).toBeInTheDocument();
    expect(screen.getByText("ar-1")).toBeInTheDocument();
    expect(screen.getByText(/Execution target:/)).toBeInTheDocument();
  });

  it("shows disabled, archived, and unselected Agents with backend-owned reasons", async () => {
    installApi({
      agentItems: [activeAgent, disabledAgent, archivedAgent, unselectedAgent],
    });
    renderPage();
    expect(
      await screen.findByRole("option", {
        name: /Coding Agent.*disabled.*Disabled — cannot be newly bound/,
      }),
    ).toBeDisabled();
    expect(
      screen.getByRole("option", {
        name: /Coding Agent.*archived.*Archived — cannot be newly bound/,
      }),
    ).toBeDisabled();
    expect(
      screen.getByRole("option", {
        name: /Coding Agent.*active.*selected revision: none.*No selected revision — cannot be newly bound/,
      }),
    ).toBeDisabled();
  });

  it("binds a Workflow while preserving disabled and unselected readiness warnings", async () => {
    installApi({
      workflowItems: [activeWorkflow, disabledWorkflow, unselectedWorkflow],
    });
    renderPage();
    const user = userEvent.setup();
    await screen.findByRole("option", { name: /Release Pipeline.*disabled/ });
    await user.selectOptions(
      screen.getByLabelText("Workflow target"),
      "w-disabled",
    );
    expect(
      screen.getByText(
        /Binding is allowed, but a future unresolved Run may fail Workflow resolution/,
      ),
    ).toBeInTheDocument();
    await user.selectOptions(
      screen.getByLabelText("Workflow target"),
      "w-unselected",
    );
    expect(
      screen.getByText(
        /Binding is allowed, but a future unresolved Run may fail Workflow resolution/,
      ),
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Bind selected Workflow" }),
    );
    expect(
      await screen.findByRole("heading", { name: "Workflow" }),
    ).toBeInTheDocument();
    expect(screen.getByText("w-unselected")).toBeInTheDocument();
  });

  it("does not offer archived Workflows as new binding targets", async () => {
    installApi({ workflowItems: [activeWorkflow, archivedWorkflow] });
    renderPage();
    expect(
      await screen.findByRole("option", {
        name: /Release Pipeline.*archived.*Archived — cannot be newly bound/,
      }),
    ).toBeDisabled();
  });

  it("requires clearing an Agent before the explicit Workflow transition", async () => {
    const fetchMock = installApi({
      agentBinding: {
        task_id: task.id,
        agent_id: activeAgent.id,
        created_at: "2026-01-01T00:00:00Z",
      },
    });
    renderPage();
    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "Agent" });
    expect(screen.getByLabelText("Workflow target")).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Bind selected Workflow" }),
    ).toBeDisabled();
    expect(
      screen.getByText("Clear Agent binding before binding a Workflow."),
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Clear Agent binding" }),
    );
    await screen.findByRole("heading", { name: "Default Friday runtime" });
    await user.selectOptions(
      screen.getByLabelText("Workflow target"),
      activeWorkflow.id,
    );
    await user.click(
      screen.getByRole("button", { name: "Bind selected Workflow" }),
    );
    expect(
      await screen.findByRole("heading", { name: "Workflow" }),
    ).toBeInTheDocument();
    const writes = fetchMock.mock.calls
      .filter(([, init]) => init?.method === "PUT")
      .map(([, init]) => JSON.parse(String(init?.body)));
    expect(writes).toEqual([
      { agent_id: null },
      { workflow_id: activeWorkflow.id },
    ]);
  });

  it("requires clearing a Workflow before the explicit Agent transition", async () => {
    const fetchMock = installApi({
      workflowBinding: {
        task_id: task.id,
        workflow_id: activeWorkflow.id,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
    });
    renderPage();
    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "Workflow" });
    expect(screen.getByLabelText("Agent target")).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Bind selected Agent" }),
    ).toBeDisabled();
    expect(
      screen.getByText("Clear Workflow binding before binding an Agent."),
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Clear Workflow binding" }),
    );
    await screen.findByRole("heading", { name: "Default Friday runtime" });
    await user.selectOptions(
      screen.getByLabelText("Agent target"),
      activeAgent.id,
    );
    await user.click(
      screen.getByRole("button", { name: "Bind selected Agent" }),
    );
    expect(
      await screen.findByRole("heading", { name: "Agent" }),
    ).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE"),
    ).toBe(true);
    const writes = fetchMock.mock.calls
      .filter(([, init]) => init?.method === "PUT")
      .map(([, init]) => JSON.parse(String(init?.body)));
    expect(writes).toEqual([{ agent_id: activeAgent.id }]);
  });

  it("fails closed when both bindings are present", async () => {
    const fetchMock = installApi({
      agentBinding: {
        task_id: task.id,
        agent_id: activeAgent.id,
        created_at: "2026-01-01T00:00:00Z",
      },
      workflowBinding: {
        task_id: task.id,
        workflow_id: activeWorkflow.id,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
    });
    renderPage();
    expect(
      await screen.findByText(
        "Task execution-target state is inconsistent. Both Agent and Workflow bindings are present.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Start Run" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Clear/ }),
    ).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([, init]) => init?.method === "PUT"),
    ).toBe(false);
    expect(
      fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE"),
    ).toBe(false);
  });

  it("starts the existing Task Run path and reports the exact Run", async () => {
    installApi();
    const { onRunStarted, onViewSchedules } = renderPage();
    const user = userEvent.setup();
    await screen.findByRole("button", { name: "Start Run" });
    await user.click(screen.getByRole("button", { name: "Start Run" }));
    await waitFor(() => expect(onRunStarted).toHaveBeenCalledWith("r-1"));
    await user.click(screen.getByRole("button", { name: "View Schedules" }));
    expect(onViewSchedules).toHaveBeenCalledWith(task.id);
  });

  it("handles Task, binding, binding mutation, and Start Run failures safely", async () => {
    installApi({ taskLoadFails: true });
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Failed to load task.",
    );

    cleanup();
    vi.restoreAllMocks();
    installApi({ bindingLoadFails: true });
    renderPage();
    expect(
      await screen.findByText(/Failed to load execution target bindings/),
    ).toBeInTheDocument();

    cleanup();
    vi.restoreAllMocks();
    installApi({ agentMutationFails: true });
    renderPage();
    const user = userEvent.setup();
    await screen.findByRole("option", { name: /Coding Agent/ });
    await user.selectOptions(
      screen.getByLabelText("Agent target"),
      activeAgent.id,
    );
    await user.click(
      screen.getByRole("button", { name: "Bind selected Agent" }),
    );
    expect(
      await screen.findByText(
        /Failed to bind the Agent. The server rejected the change/,
      ),
    ).toBeInTheDocument();

    cleanup();
    vi.restoreAllMocks();
    const { onRunStarted } = (() => {
      installApi({ startRunFails: true });
      return renderPage();
    })();
    await screen.findByRole("button", { name: "Start Run" });
    await user.click(screen.getByRole("button", { name: "Start Run" }));
    expect(
      await screen.findByText(
        /Failed to start the Run. The server rejected this Task/,
      ),
    ).toBeInTheDocument();
    expect(onRunStarted).not.toHaveBeenCalled();
  });
});
