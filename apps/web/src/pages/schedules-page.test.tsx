import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SchedulesPage } from "./schedules-page";

function response(body: unknown) {
  return new Response(JSON.stringify(body), {
    headers: { "content-type": "application/json" },
  });
}

const schedule = (id: string) => ({
  id,
  task_id: "task-1",
  kind: "cron",
  cron: id,
  run_at: null,
  timezone: "UTC",
  status: "active",
  next_fire_at: "2026-01-02T09:00:00Z",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
});

function renderPage(onViewRun = vi.fn()) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <SchedulesPage
        taskId="task-1"
        onBack={() => undefined}
        onViewRun={onViewRun}
      />
    </QueryClientProvider>,
  );
  return { onViewRun };
}

describe("SchedulesPage", () => {
  afterEach(() => vi.restoreAllMocks());

  it("follows schedule cursors instead of silently showing only the first 100", async () => {
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(
        response({ items: [schedule("s-1")], next_cursor: "page-2" }),
      )
      .mockResolvedValueOnce(
        response({ items: [schedule("s-2")], next_cursor: null }),
      );
    renderPage();

    expect(await screen.findByText(/s-1/)).toBeInTheDocument();
    expect(await screen.findByText(/s-2/)).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const secondCall = fetchMock.mock.calls[1];
    if (!secondCall) throw new Error("expected a second cursor request");
    expect(String(secondCall[0])).toContain("cursor=page-2");
  });

  it("creates a one-time schedule and refreshes the list", async () => {
    const created = {
      ...schedule("s-new"),
      kind: "once",
      cron: null,
      run_at: "2026-08-01T09:00:00Z",
    };
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(response({ items: [], next_cursor: null }))
      .mockResolvedValueOnce(response(created))
      .mockResolvedValue(response({ items: [created], next_cursor: null }));
    renderPage();
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("Run at"), "2026-08-01T09:00");
    await user.click(screen.getByRole("button", { name: "Create schedule" }));

    expect(await screen.findByText(/2026-08-01T09:00:00Z/)).toBeInTheDocument();
  });

  it("updates schedule controls and prevents duplicate actions", async () => {
    let resolveControl: ((value: Response) => void) | undefined;
    const pending = new Promise<Response>((resolve) => {
      resolveControl = resolve;
    });
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(
        response({ items: [schedule("s-1")], next_cursor: null }),
      )
      .mockReturnValueOnce(pending)
      .mockResolvedValue(
        response({
          items: [{ ...schedule("s-1"), status: "paused" }],
          next_cursor: null,
        }),
      );
    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Pause" }));
    expect(screen.getByRole("button", { name: "Pause" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();

    resolveControl?.(response({ ...schedule("s-1"), status: "paused" }));
    expect(await screen.findByRole("button", { name: "Resume" })).toBeVisible();
  });

  it("lists fires and navigates to their run", async () => {
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(
        response({ items: [schedule("s-1")], next_cursor: null }),
      )
      .mockResolvedValueOnce(
        response({
          items: [
            {
              id: "f-1",
              schedule_id: "s-1",
              scheduled_for: "2026-01-02T09:00:00Z",
              fired_at: "2026-01-02T09:00:01Z",
              run_id: "r-1",
            },
          ],
          next_cursor: null,
        }),
      );
    const { onViewRun } = renderPage();
    const user = userEvent.setup();

    await user.click(
      await screen.findByRole("button", { name: "Inspect fires" }),
    );
    await user.click(await screen.findByRole("button", { name: "run r-1" }));

    expect(onViewRun).toHaveBeenCalledWith("r-1");
  });
});
