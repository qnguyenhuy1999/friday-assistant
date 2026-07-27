import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConversationPage } from "./conversation-page";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** Routes the page's calls, holding the turn submission open so an interrupt
 * can land while the request is still in flight. */
function stubApi() {
  const cancelled: string[] = [];
  let releaseSubmit: (() => void) | null = null;
  const submitted = new Promise<void>((resolve) => {
    releaseSubmit = resolve;
  });
  let allowSubmit: (() => void) | null = null;
  const gate = new Promise<void>((resolve) => {
    allowSubmit = resolve;
  });
  vi.spyOn(global, "fetch").mockImplementation(async (input, init) => {
    const url = String(input instanceof Request ? input.url : input);
    const method = (
      init?.method ?? (input instanceof Request ? input.method : "GET")
    ).toUpperCase();
    if (url.includes("/conversations") && url.endsWith("/turns")) {
      if (method === "POST") {
        releaseSubmit?.();
        await gate;
        return jsonResponse({
          id: "t-1",
          conversation_id: "c-1",
          run_id: "run-1",
          task_id: "task-1",
          client_turn_id: "x",
          input_text: "hello",
          input_mode: "typed",
          recognition_language: null,
          created_at: "2026-01-01T00:00:00Z",
        });
      }
      return jsonResponse({ items: [], next_cursor: null });
    }
    if (url.includes("/conversations"))
      return jsonResponse({
        id: "c-1",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      });
    if (/\/runs\/[^/]+\/cancel$/.test(url)) {
      const runId = url.split("/runs/")[1]!.replace("/cancel", "");
      cancelled.push(runId);
      return jsonResponse({
        id: runId,
        task_id: "task-1",
        status: "cancelled",
        created_at: "2026-01-01T00:00:00Z",
        failure: null,
        execution_id: runId,
      });
    }
    const parsedUrl = new URL(url, "http://127.0.0.1:8000");
    const runMatch = /^\/v1\/runs\/([^/]+)$/.exec(parsedUrl.pathname);
    if (runMatch) {
      return jsonResponse({
        id: runMatch[1],
        task_id: "task-1",
        status: "running",
        created_at: "2026-01-01T00:00:00Z",
        failure: null,
        execution_id: runMatch[1],
      });
    }
    const executionMatch = /^\/v1\/runs\/([^/]+)\/execution$/.exec(
      parsedUrl.pathname,
    );
    if (executionMatch) {
      const runId = executionMatch[1];
      return jsonResponse({
        items: [
          {
            id: runId,
            task_id: "task-1",
            status: "running",
            created_at: "2026-01-01T00:00:00Z",
            failure: null,
            execution_id: runId,
          },
        ],
        next_cursor: null,
      });
    }
    return jsonResponse({ items: [], next_cursor: null });
  });
  return {
    cancelled,
    submitted,
    releaseServer: () => allowSubmit?.(),
  };
}

function renderPage() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ConversationPage onReviewApproval={() => undefined} />
    </QueryClientProvider>,
  );
}

describe("ConversationPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("cancels a run whose submission resolves after the user interrupted", async () => {
    const api = stubApi();
    const user = userEvent.setup();
    renderPage();

    await user.type(await screen.findByLabelText("Message"), "hello");
    await user.click(screen.getByRole("button", { name: "Send" }));
    // The POST is in flight and has no run id yet, so Escape has nothing local
    // to cancel — this is exactly the window the fence has to cover.
    await api.submitted;
    await user.keyboard("{Escape}");

    api.releaseServer();

    // The run the user walked away from must not be left running.
    await waitFor(() => expect(api.cancelled).toEqual(["run-1"]));
  }, 20_000);
});
