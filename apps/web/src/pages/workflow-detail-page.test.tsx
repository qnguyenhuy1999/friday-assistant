import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WorkflowDetailPage } from "./workflow-detail-page";

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const workflow: {
  id: string;
  key: string;
  display_name: string;
  description: string;
  status: string;
  active_revision_id: string | null;
  created_at: string;
  updated_at: string;
} = {
  id: "w-1",
  key: "release.pipeline",
  display_name: "Release pipeline",
  description: "Coordinates a release.",
  status: "active",
  active_revision_id: "wr-1",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
};
const agentA = {
  id: "a-1",
  key: "researcher",
  display_name: "Researcher",
  description: "Finds facts.",
  status: "active",
  active_revision_id: "ar-1",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};
const agentB = {
  id: "a-2",
  key: "coder",
  display_name: "Coder",
  description: "Writes code.",
  status: "disabled",
  active_revision_id: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};
const revision = {
  id: "wr-1",
  workflow_id: "w-1",
  version: 1,
  content_sha256: "a".repeat(64),
  source_kind: "operator",
  nodes: [
    {
      id: "wn-1",
      revision_id: "wr-1",
      node_key: "analyze",
      target_agent_id: "a-1",
      objective: "Analyze the change.",
      input_payload: { repository: "friday" },
      expected_output_contract: "A concise analysis.",
      created_at: "2026-01-01T00:00:00Z",
    },
    {
      id: "wn-2",
      revision_id: "wr-1",
      node_key: "implement",
      target_agent_id: "a-2",
      objective: "Implement the change.",
      input_payload: ["analysis"],
      expected_output_contract: "A tested patch.",
      created_at: "2026-01-01T00:00:00Z",
    },
  ],
  edges: [
    {
      id: "we-1",
      revision_id: "wr-1",
      from: "analyze",
      to: "implement",
      created_at: "2026-01-01T00:00:00Z",
    },
  ],
  created_at: "2026-01-01T00:00:00Z",
};
function renderPage() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <WorkflowDetailPage workflowId="w-1" onBack={() => undefined} />
    </QueryClientProvider>,
  );
}

function mockDetailApi(
  currentWorkflow = workflow,
  revisions = [revision],
  createResponse = revision,
) {
  return vi.spyOn(global, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init as RequestInit | undefined)?.method ?? "GET";
    if ((url.endsWith("/agents") || url.includes("/agents?")) && method === "GET")
      return response({ items: [agentA, agentB], next_cursor: null });
    if (url.endsWith("/revisions") && method === "POST")
      return response(createResponse, 201);
    if (url.includes("/revisions?") && method === "GET")
      return response(revisions);
    if (url.endsWith("/activate") && method === "POST")
      return response({
        ...currentWorkflow,
        status: "active",
        active_revision_id: "wr-1",
      });
    if (url.endsWith("/disable") && method === "POST")
      return response({ ...currentWorkflow, status: "disabled" });
    if (url.endsWith("/archive") && method === "POST")
      return response({ ...currentWorkflow, status: "archived" });
    if (url.endsWith("/workflows/w-1") && method === "GET")
      return response(currentWorkflow);
    return response({ error: { type: "unexpected", message: url } }, 500);
  });
}

describe("WorkflowDetailPage", () => {
  beforeEach(() =>
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    ),
  );
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders lifecycle and immutable revision provenance", async () => {
    mockDetailApi();
    renderPage();
    expect(
      await screen.findByRole("heading", { name: "Release pipeline" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Lifecycle status").nextElementSibling,
    ).toHaveTextContent("active");
    expect(
      screen.getByText("Workflow ID").nextElementSibling,
    ).toHaveTextContent("w-1");
    expect(screen.getByText("Content SHA-256")).toBeInTheDocument();
    expect(screen.getByText("v1 — active")).toBeInTheDocument();
    expect(
      screen.getByText("Researcher · researcher (a-1)"),
    ).toBeInTheDocument();
    expect(screen.getByText("analyze → implement")).toBeInTheDocument();
  });

  it("renders a disabled Workflow as selected and keeps the same revision activatable", async () => {
    const fetchMock = mockDetailApi({ ...workflow, status: "disabled" });
    renderPage();
    expect(await screen.findByText("v1 — selected")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Activate v1" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Disable Workflow" }),
    ).not.toBeInTheDocument();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Activate v1" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).endsWith("/revisions/wr-1/activate"),
        ),
      ).toBe(true),
    );
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          String(input).endsWith("/revisions") &&
          (init as RequestInit | undefined)?.method === "POST",
      ),
    ).toBe(false);
  });

  it("creates a valid revision with the exact SDK contract and does not activate it", async () => {
    const fetchMock = mockDetailApi(
      { ...workflow, active_revision_id: null },
      [],
      revision,
    );
    renderPage();
    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "Release pipeline" });
    await user.click(screen.getByRole("button", { name: "Add node" }));
    await user.click(screen.getByRole("button", { name: "Add node" }));
    const keys = screen.getAllByLabelText("Node key");
    await user.clear(keys[0]!);
    await user.type(keys[0]!, "analyze");
    await user.clear(keys[1]!);
    await user.type(keys[1]!, "implement");
    const targetAgents = screen.getAllByLabelText("Target Agent");
    await user.selectOptions(targetAgents[0]!, "a-1");
    await user.selectOptions(targetAgents[1]!, "a-2");
    expect(
      screen.getByText(
        /Warning: Coder is disabled and has no selected revision\./,
      ),
    ).toBeInTheDocument();
    const objectives = screen.getAllByLabelText("Objective");
    await user.type(objectives[0]!, "Analyze the change.");
    await user.type(objectives[1]!, "Implement the change.");
    const payloads = screen.getAllByLabelText("Input payload (JSON)");
    await user.clear(payloads[0]!);
    fireEvent.change(payloads[0]!, {
      target: { value: '{"repository":"friday"}' },
    });
    await user.clear(payloads[1]!);
    fireEvent.change(payloads[1]!, { target: { value: '["analysis"]' } });
    const outputContracts = screen.getAllByLabelText(
      "Expected output contract",
    );
    await user.type(outputContracts[0]!, "A concise analysis.");
    await user.type(outputContracts[1]!, "A tested patch.");
    await user.click(screen.getByRole("button", { name: "Add edge" }));
    await user.click(
      screen.getByRole("button", { name: "Create immutable revision" }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Created revision v1",
    );
    const createCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith("/revisions") &&
        (init as RequestInit | undefined)?.method === "POST",
    );
    expect(createCall).toBeDefined();
    expect(JSON.parse(String((createCall?.[1] as RequestInit).body))).toEqual({
      nodes: [
        {
          node_key: "analyze",
          target_agent_id: "a-1",
          objective: "Analyze the change.",
          input_payload: { repository: "friday" },
          expected_output_contract: "A concise analysis.",
        },
        {
          node_key: "implement",
          target_agent_id: "a-2",
          objective: "Implement the change.",
          input_payload: ["analysis"],
          expected_output_contract: "A tested patch.",
        },
      ],
      edges: [{ from: "analyze", to: "implement" }],
      source_kind: "operator",
    });
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).endsWith("/activate"),
      ),
    ).toBe(false);
  });

  it("rejects zero nodes and malformed input JSON before submission", async () => {
    const fetchMock = mockDetailApi(
      { ...workflow, active_revision_id: null },
      [],
    );
    renderPage();
    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "Release pipeline" });
    await user.click(
      screen.getByRole("button", { name: "Create immutable revision" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "at least one node",
    );
    await user.click(screen.getByRole("button", { name: "Add node" }));
    await user.selectOptions(screen.getByLabelText("Target Agent"), "a-1");
    await user.type(screen.getByLabelText("Objective"), "Analyze it.");
    await user.clear(screen.getByLabelText("Input payload (JSON)"));
    await user.type(screen.getByLabelText("Input payload (JSON)"), "not-json");
    await user.type(
      screen.getByLabelText("Expected output contract"),
      "A result.",
    );
    await user.click(
      screen.getByRole("button", { name: "Create immutable revision" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("valid JSON");
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          String(input).endsWith("/revisions") &&
          (init as RequestInit | undefined)?.method === "POST",
      ),
    ).toBe(false);
  });

  it("loads older Workflow revisions in bounded pages", async () => {
    const revisions = Array.from({ length: 10 }, (_, index) => ({
      ...revision,
      id: `wr-${20 - index}`,
      version: 20 - index,
    }));
    const fetchMock = vi.spyOn(global, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/workflows/w-1")) return response(workflow);
      if (url.endsWith("/agents") || url.includes("/agents?"))
        return response({ items: [agentA], next_cursor: null });
      if (url.includes("before_version=11"))
        return response([{ ...revision, id: "wr-10", version: 10 }]);
      if (url.includes("/revisions?")) return response(revisions);
      return response({ error: { type: "unexpected", message: url } }, 500);
    });
    renderPage();
    expect(await screen.findByText("v20")).toBeInTheDocument();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Load older revisions" }));
    expect(await screen.findByText("v10")).toBeInTheDocument();
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).includes("before_version=11"),
        ),
      ).toBe(true),
    );
  });

  it("keeps archived Workflows read-only while showing immutable history", async () => {
    const archived = { ...workflow, status: "archived" };
    mockDetailApi(archived);
    renderPage();
    expect(await screen.findByText("v1 — selected")).toBeInTheDocument();
    expect(screen.getByText(/archived and read-only/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Activate v1" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Disable Workflow" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Archive Workflow" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Create immutable revision" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("implement")).toBeInTheDocument();
  });

  it("shows a safe detail error state", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      response({ error: { type: "unavailable", message: "internal" } }, 503),
    );
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Failed to load Workflow.",
    );
  });
});
