import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConversationPage } from "./conversation-page";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

type RouteTable = {
  turns?: unknown[] | (() => unknown[]);
  run?: (runId: string) => unknown;
  holdSubmit?: boolean;
  includeSubmittedTurn?: boolean;
};

function run(id: string, status: string) {
  return {
    id,
    task_id: "task-1",
    status,
    created_at: "2026-01-01T00:00:00Z",
    failure: null,
    execution_id: id,
  };
}

function turn(runId = "run-1") {
  return {
    id: "t-1",
    conversation_id: "c-1",
    run_id: runId,
    task_id: "task-1",
    client_turn_id: "x",
    input_text: "hello",
    input_mode: "typed",
    recognition_language: null,
    created_at: "2026-01-01T00:00:00Z",
  };
}

/** Routes the page's calls. Individual tests override only their scenario. */
function stubApi(routes: RouteTable = {}) {
  const cancelled: string[] = [];
  let releaseSubmit: (() => void) | null = null;
  let didSubmit = false;
  const submitted = new Promise<void>((resolve) => {
    releaseSubmit = resolve;
  });
  let allowSubmit: (() => void) | null = null;
  const gate = new Promise<void>((resolve) => {
    allowSubmit = resolve;
  });
  vi.spyOn(global, "fetch").mockImplementation(async (input, init) => {
    const url = String(input instanceof Request ? input.url : input);
    const pathname = new URL(url, "http://127.0.0.1:8000").pathname;
    const method = (
      init?.method ?? (input instanceof Request ? input.method : "GET")
    ).toUpperCase();
    if (pathname.includes("/conversations") && pathname.endsWith("/turns")) {
      if (method === "POST") {
        releaseSubmit?.();
        if (routes.holdSubmit) await gate;
        didSubmit = true;
        return jsonResponse(turn());
      }
      return jsonResponse({
        items:
          routes.includeSubmittedTurn && didSubmit
            ? [turn()]
            : typeof routes.turns === "function"
              ? routes.turns()
              : (routes.turns ?? []),
        next_cursor: null,
      });
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
      return jsonResponse(
        routes.run?.(runMatch[1]!) ?? run(runMatch[1]!, "running"),
      );
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
    const resultMatch = /^\/v1\/runs\/([^/]+)\/result$/.exec(
      parsedUrl.pathname,
    );
    if (resultMatch)
      return jsonResponse({ summary: "E2E task completed", details: null });
    return jsonResponse({ items: [], next_cursor: null });
  });
  return {
    cancelled,
    submitted,
    releaseServer: () => allowSubmit?.(),
  };
}

class FakeSpeechRecognition {
  lang = "";
  continuous = false;
  interimResults = false;
  maxAlternatives = 0;
  onresult: (() => void) | null = null;
  onend: (() => void) | null = null;
  onerror: (() => void) | null = null;
  start() {}
  stop() {
    this.onend?.();
  }
  abort() {}
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

  it("recovers a processing run after reload", async () => {
    stubApi({ turns: [turn()], run: (id) => run(id, "running") });
    renderPage();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Send" })).toBeDisabled(),
    );
  });

  it("recovers an approval pause after reload", async () => {
    stubApi({
      turns: [turn()],
      run: (id) => run(id, "waiting_for_approval"),
    });
    renderPage();

    expect(await screen.findByText("Approval required")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Review approval" }),
    ).toBeInTheDocument();
  });

  it("adopts an answer already settled when submission resolves", async () => {
    const api = stubApi({
      includeSubmittedTurn: true,
      run: (id) => run(id, "succeeded"),
    });
    const user = userEvent.setup();
    renderPage();

    await user.type(await screen.findByLabelText("Message"), "hello");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await api.submitted;

    await waitFor(() =>
      expect(screen.getByText("E2E task completed")).toBeInTheDocument(),
    );
    expect(screen.getByText("Ready")).toBeInTheDocument();
  });

  it("adopts an approval pause already present when submission resolves", async () => {
    const api = stubApi({
      includeSubmittedTurn: true,
      run: (id) => run(id, "waiting_for_approval"),
    });
    const user = userEvent.setup();
    renderPage();

    await user.type(await screen.findByLabelText("Message"), "hello");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await api.submitted;

    expect(await screen.findByText("Approval required")).toBeInTheDocument();
  });

  it("releases keyboard PTT once after a modified Space keydown", async () => {
    stubApi();
    Object.defineProperty(window, "SpeechRecognition", {
      configurable: true,
      value: FakeSpeechRecognition,
    });
    renderPage();

    fireEvent.keyDown(window, { code: "Space" });
    expect(await screen.findByText("Listening…")).toBeInTheDocument();
    fireEvent.keyDown(window, { code: "Space", ctrlKey: true });
    expect(screen.getByText("Listening…")).toBeInTheDocument();
    fireEvent.keyUp(window, { code: "Space" });
    await waitFor(() => expect(screen.getByText("Ready")).toBeInTheDocument());
  });

  it("releases keyboard PTT when the window loses focus", async () => {
    stubApi();
    Object.defineProperty(window, "SpeechRecognition", {
      configurable: true,
      value: FakeSpeechRecognition,
    });
    renderPage();

    fireEvent.keyDown(window, { code: "Space" });
    expect(await screen.findByText("Listening…")).toBeInTheDocument();
    window.dispatchEvent(new Event("blur"));
    await waitFor(() => expect(screen.getByText("Ready")).toBeInTheDocument());
  });

  it("cancels a run whose submission resolves after the user interrupted", async () => {
    const api = stubApi({ holdSubmit: true });
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
