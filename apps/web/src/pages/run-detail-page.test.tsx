import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunDetailPage } from "./run-detail-page";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const page = (items: unknown[]) => ({ items, next_cursor: null });

const step = {
  id: "s-1",
  run_id: "r-1",
  name: "clone repo",
  position: 0,
  status: "succeeded",
  failure: null,
};
const invocation = {
  invocation_id: "i-1",
  run_id: "r-1",
  step_id: null,
  tool_name: "shell.run",
  status: "succeeded",
  requested_at: "x",
  approval_request_id: null,
  output: null,
  output_set: true,
  failure: null,
};
const artifact = {
  artifact_id: "a-1",
  run_id: "r-1",
  step_id: null,
  kind: "file",
  name: "repo",
  media_type: "inode/directory",
  location: "/tmp/repo",
  created_at: "x",
  size: null,
  checksum: null,
  metadata: null,
};
const approval = {
  approval_id: "ap-1",
  run_id: "r-1",
  step_id: null,
  category: "computer_use",
  summary: "Click Send",
  reason: "explicit sign-off",
  requested_action: "computer.click",
  requested_input: { pid: 1 },
  status: "pending",
  requested_at: "x",
  expires_at: null,
  resolved_at: null,
  resolution_note: null,
  resolver: null,
  authorization_fingerprint: null,
  consumed_at: null,
  subject_kind: "run",
  subject_id: "r-1",
};

/** Routes each of the page's six concurrent reads to its own fixture. */
function mockApi(run: unknown, approvals: unknown[] = []) {
  vi.spyOn(global, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("/steps")) return jsonResponse(page([step]));
    if (url.includes("/tool-invocations"))
      return jsonResponse(page([invocation]));
    if (url.includes("/artifacts")) return jsonResponse(page([artifact]));
    if (url.includes("/approvals")) return jsonResponse(page(approvals));
    if (url.includes("/events")) return jsonResponse(page([]));
    if (url.endsWith("/agent"))
      return jsonResponse({
        run_id: "r-1",
        resolved: true,
        resolved_at: "2026-01-01T00:00:00Z",
        agent_id: "a-1",
        revision_id: "r-3",
      });
    return jsonResponse(run as object);
  });
}

function renderPage() {
  const onViewApprovals = vi.fn();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <RunDetailPage runId="r-1" onViewApprovals={onViewApprovals} />
    </QueryClientProvider>,
  );
  return { onViewApprovals };
}

const runningRun = {
  id: "r-1",
  task_id: "t-1",
  status: "running",
  created_at: "x",
  failure: null,
  execution_id: "exec-1",
};

describe("RunDetailPage", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the run status, steps, invocations, and artifacts", async () => {
    mockApi(runningRun);
    renderPage();
    expect(await screen.findByText("Status: running")).toBeInTheDocument();
    expect(await screen.findByText(/clone repo/)).toBeInTheDocument();
    expect(await screen.findByText(/shell\.run/)).toBeInTheDocument();
    expect(await screen.findByText(/\/tmp\/repo/)).toBeInTheDocument();
  });

  it("renders existing frozen Agent provenance read-only", async () => {
    mockApi(runningRun);
    renderPage();
    expect(
      await screen.findByText("Frozen Agent provenance"),
    ).toBeInTheDocument();
    expect(await screen.findByText("a-1")).toBeInTheDocument();
    expect(screen.getByText("r-3")).toBeInTheDocument();
  });

  it("links to the approvals view only while an approval is pending", async () => {
    mockApi(runningRun, [approval]);
    const { onViewApprovals } = renderPage();
    const banner = await screen.findByText(/1 pending approval/);
    expect(banner).toBeInTheDocument();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "View approvals" }));
    expect(onViewApprovals).toHaveBeenCalled();
  });

  it("shows no approvals banner when none are pending", async () => {
    mockApi(runningRun, [{ ...approval, status: "approved" }]);
    renderPage();
    await screen.findByText("Status: running");
    expect(screen.queryByText(/pending approval/)).not.toBeInTheDocument();
  });

  it("shows the final result once the run is terminal", async () => {
    mockApi({ ...runningRun, status: "succeeded" });
    renderPage();
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Run succeeded.",
    );
  });

  it("shows an error when the run request fails", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      jsonResponse(
        {
          error: { type: "run_not_found", message: "no such run", details: {} },
        },
        404,
      ),
    );
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Failed to load run.",
    );
  });
});
