import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkflowsPage } from "./workflows-page";

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const workflow = {
  id: "w-1",
  key: "release.pipeline",
  display_name: "Release pipeline",
  description: "Coordinates a release.",
  status: "active",
  active_revision_id: "wr-1",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
};

function renderPage(onViewWorkflow = vi.fn()) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <WorkflowsPage onViewWorkflow={onViewWorkflow} />
    </QueryClientProvider>,
  );
  return { onViewWorkflow };
}

describe("WorkflowsPage", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders registry results and opens the exact Workflow", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      response({ items: [workflow], next_cursor: null }),
    );
    const { onViewWorkflow } = renderPage();
    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "Release pipeline" }));
    expect(screen.getByLabelText("Workflow registry")).toHaveTextContent(
      "key: release.pipeline",
    );
    expect(screen.getByLabelText("Workflow registry")).toHaveTextContent(
      "selected revision: wr-1",
    );
    expect(onViewWorkflow).toHaveBeenCalledWith("w-1");
  });

  it("renders empty and SDK error states", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      response({ items: [], next_cursor: null }),
    );
    renderPage();
    expect(await screen.findByText(/No Workflows yet/)).toBeInTheDocument();

    vi.restoreAllMocks();
    vi.spyOn(global, "fetch").mockResolvedValue(
      response({ error: { type: "unavailable", message: "no" } }, 503),
    );
    renderPage();
    expect((await screen.findAllByRole("alert")).at(-1)).toHaveTextContent(
      "Failed to load Workflows.",
    );
  });

  it("creates a Workflow with the exact contract body and navigates to it", async () => {
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(response({ items: [], next_cursor: null }))
      .mockResolvedValueOnce(response(workflow, 201));
    const { onViewWorkflow } = renderPage();
    const user = userEvent.setup();
    await screen.findByText(/No Workflows yet/);
    await user.type(screen.getByLabelText("Key"), " release.pipeline ");
    await user.type(
      screen.getByLabelText("Display name"),
      " Release pipeline ",
    );
    await user.type(
      screen.getByLabelText("Description"),
      " Coordinates a release. ",
    );
    await user.click(screen.getByRole("button", { name: "Create workflow" }));
    expect(onViewWorkflow).toHaveBeenCalledWith("w-1");
    const request = fetchMock.mock.calls[1];
    expect(request).toBeDefined();
    expect(JSON.parse(String((request?.[1] as RequestInit).body))).toEqual({
      key: "release.pipeline",
      display_name: "Release pipeline",
      description: "Coordinates a release.",
    });
  });

  it("respects cursor pagination with Load more", async () => {
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(
        response({ items: [workflow], next_cursor: "page-2" }),
      )
      .mockResolvedValueOnce(
        response({
          items: [{ ...workflow, id: "w-2", display_name: "Deploy" }],
          next_cursor: null,
        }),
      );
    renderPage();
    expect(
      await screen.findByRole("button", { name: "Release pipeline" }),
    ).toBeInTheDocument();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Load more" }));
    expect(
      await screen.findByRole("button", { name: "Deploy" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain("cursor=page-2");
  });
});
