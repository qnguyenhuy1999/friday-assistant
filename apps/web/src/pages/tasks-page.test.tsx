import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TasksPage } from "./tasks-page";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const task = {
  id: "t-1",
  title: "Ship it",
  description: "",
  status: "active",
  created_at: "2026-07-26T00:00:00Z",
  failure: null,
};

function renderPage(onViewTask = vi.fn()) {
  const onRunStarted = vi.fn();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <TasksPage onRunStarted={onRunStarted} onViewTask={onViewTask} />
    </QueryClientProvider>,
  );
  return { onRunStarted, onViewTask };
}

describe("TasksPage", () => {
  afterEach(() => vi.restoreAllMocks());

  it("lists existing tasks", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      jsonResponse({ items: [task], next_cursor: null }),
    );
    renderPage();
    expect(await screen.findByText(/Ship it/)).toBeInTheDocument();
  });

  it("opens the exact Task from the registry", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      jsonResponse({ items: [task], next_cursor: null }),
    );
    const { onViewTask } = renderPage();
    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "Ship it" }));
    expect(onViewTask).toHaveBeenCalledWith("t-1");
  });

  it("loads later task pages on demand", async () => {
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(
        jsonResponse({ items: [task], next_cursor: "page-2" }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          items: [{ ...task, id: "t-2", title: "Later task" }],
          next_cursor: null,
        }),
      );
    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Load more" }));

    expect(await screen.findByText(/Later task/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain("cursor=page-2");
  });

  it("creates a task and clears the input", async () => {
    const fetchMock = vi.spyOn(global, "fetch");
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ items: [], next_cursor: null }),
    );
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ ...task, id: "t-2", title: "New task" }),
    );
    fetchMock.mockResolvedValue(
      jsonResponse({
        items: [{ ...task, id: "t-2", title: "New task" }],
        next_cursor: null,
      }),
    );

    renderPage();
    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "Tasks" });
    await user.type(screen.getByLabelText("Title"), "New task");
    await user.click(screen.getByRole("button", { name: "Create task" }));

    expect(await screen.findByText(/New task/)).toBeInTheDocument();
    expect(screen.getByLabelText("Title")).toHaveValue("");
  });

  it("does not submit an empty or whitespace-only title", async () => {
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValue(jsonResponse({ items: [], next_cursor: null }));
    renderPage();
    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "Tasks" });
    await user.type(screen.getByLabelText("Title"), "   ");
    await user.click(screen.getByRole("button", { name: "Create task" }));
    const posts = fetchMock.mock.calls.filter(
      ([, init]) => init?.method === "POST",
    );
    expect(posts).toEqual([]);
  });

  it("reports the new run id when a run starts", async () => {
    const fetchMock = vi.spyOn(global, "fetch");
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ items: [task], next_cursor: null }),
    );
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ task_id: "t-1", run_id: "r-9" }),
    );
    fetchMock.mockResolvedValue(
      jsonResponse({ items: [task], next_cursor: null }),
    );

    const { onRunStarted } = renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Start run" }));
    await waitFor(() => expect(onRunStarted).toHaveBeenCalledWith("r-9"));
  });

  it("surfaces a failed start without navigating away", async () => {
    const fetchMock = vi.spyOn(global, "fetch");
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ items: [task], next_cursor: null }),
    );
    fetchMock.mockResolvedValue(
      jsonResponse(
        { error: { type: "entity_conflict", message: "boom", details: {} } },
        409,
      ),
    );

    const { onRunStarted } = renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Start run" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Failed to start the run.",
    );
    expect(onRunStarted).not.toHaveBeenCalled();
  });

  it("shows an error when the list request fails", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      jsonResponse(
        { error: { type: "internal_error", message: "boom", details: {} } },
        500,
      ),
    );
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Failed to load tasks.",
    );
  });
});
