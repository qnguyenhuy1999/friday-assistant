import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgentDetailPage } from "./agent-detail-page";

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const agent = {
  id: "a-1",
  key: "coder",
  display_name: "Coder",
  description: "Writes code",
  status: "active",
  active_revision_id: "r-1",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
};
const revision = {
  id: "r-1",
  agent_id: "a-1",
  version: 1,
  instructions: "Write safely.",
  runtime_kind: "claude_cli",
  runtime_config: {},
  content_sha256: "a".repeat(64),
  source_kind: "operator",
  created_at: "2026-01-01T00:00:00Z",
};

function mockDetailApi() {
  return vi.spyOn(global, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.endsWith("/revisions")) return response([revision]);
    if (url.endsWith("/agents/a-1")) return response(agent);
    return response({ error: { type: "unexpected", message: url } }, 500);
  });
}

function renderPage() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <AgentDetailPage agentId="a-1" onBack={() => undefined} />
    </QueryClientProvider>,
  );
}

describe("AgentDetailPage", () => {
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
      await screen.findByRole("heading", { name: "Coder" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Lifecycle status").nextElementSibling,
    ).toHaveTextContent("active");
    expect(await screen.findByText("Content SHA-256")).toBeInTheDocument();
    expect(screen.getByText("v1 — active")).toBeInTheDocument();
    expect(screen.getByText(/cannot be edited in place/)).toBeInTheDocument();
  });

  it("blocks malformed runtime configuration before it reaches the SDK", async () => {
    const fetchMock = mockDetailApi();
    renderPage();
    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "Coder" });
    await user.type(
      screen.getByLabelText("Instructions"),
      "Use the safe path.",
    );
    await user.clear(
      screen.getByLabelText("Runtime configuration (JSON object)"),
    );
    await user.type(
      screen.getByLabelText("Runtime configuration (JSON object)"),
      "not-json",
    );
    await user.click(
      screen.getByRole("button", { name: "Create immutable revision" }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Runtime configuration must be valid JSON.",
    );
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("creates a new revision without activating it", async () => {
    const fetchMock = mockDetailApi();
    renderPage();
    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "Coder" });
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (
        url.endsWith("/revisions") &&
        (init as RequestInit | undefined)?.method === "POST"
      )
        return response({ ...revision, id: "r-2", version: 2 }, 201);
      if (url.endsWith("/revisions"))
        return response([revision, { ...revision, id: "r-2", version: 2 }]);
      if (url.endsWith("/agents/a-1")) return response(agent);
      return response({ error: { type: "unexpected", message: url } }, 500);
    });
    await user.type(screen.getByLabelText("Instructions"), "Version two.");
    await user.click(
      screen.getByRole("button", { name: "Create immutable revision" }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Created revision v2. It is not active until activated.",
    );
    const createCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith("/revisions") &&
        (init as RequestInit | undefined)?.method === "POST",
    );
    expect(createCall).toBeDefined();
    expect(JSON.parse(String((createCall?.[1] as RequestInit).body))).toEqual({
      instructions: "Version two.",
      runtime_kind: "claude_cli",
      runtime_config: {},
      source_kind: "operator",
    });
  });

  it("activates the selected revision and lifecycle controls target the Agent", async () => {
    const fetchMock = mockDetailApi();
    const revisionTwo = { ...revision, id: "r-2", version: 2 };
    fetchMock.mockClear();
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/revisions")) return response([revision, revisionTwo]);
      if (url.endsWith("/activate"))
        return response({ ...agent, active_revision_id: "r-2" });
      if (url.endsWith("/disable"))
        return response({ ...agent, status: "disabled" });
      if (url.endsWith("/archive"))
        return response({ ...agent, status: "archived" });
      if (url.endsWith("/agents/a-1")) return response(agent);
      return response(
        { error: { type: "unexpected", message: String(init) } },
        500,
      );
    });
    renderPage();
    const user = userEvent.setup();
    await screen.findByRole("button", { name: "Activate v2" });
    await user.click(screen.getByRole("button", { name: "Activate v2" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) =>
          String(url).endsWith("/revisions/r-2/activate"),
        ),
      ).toBe(true),
    );
    await user.click(screen.getByRole("button", { name: "Disable Agent" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) =>
          String(url).endsWith("/agents/a-1/disable"),
        ),
      ).toBe(true),
    );
    await user.click(screen.getByRole("button", { name: "Archive Agent" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) =>
          String(url).endsWith("/agents/a-1/archive"),
        ),
      ).toBe(true),
    );
  });
});
