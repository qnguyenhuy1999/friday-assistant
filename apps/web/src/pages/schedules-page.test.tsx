import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
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
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <SchedulesPage
          taskId="task-1"
          onBack={() => undefined}
          onViewRun={() => undefined}
        />
      </QueryClientProvider>,
    );

    expect(await screen.findByText(/s-1/)).toBeInTheDocument();
    expect(await screen.findByText(/s-2/)).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const secondCall = fetchMock.mock.calls[1];
    if (!secondCall) throw new Error("expected a second cursor request");
    expect(String(secondCall[0])).toContain("cursor=page-2");
  });
});
