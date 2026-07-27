import type { ConversationTurn } from "@friday/contracts";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ANSWER_WINDOW_TURNS,
  MAX_ANSWER_EVENT_PAGES,
  useConversationAnswers,
} from "./use-conversation-answers";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function turn(index: number): ConversationTurn {
  return {
    id: `turn-${index}`,
    conversation_id: "c-1",
    client_turn_id: `client-${index}`,
    input_text: `message ${index}`,
    input_mode: "typed",
    recognition_language: null,
    task_id: `task-${index}`,
    run_id: `run-${index}`,
    created_at: "2026-07-26T00:00:00Z",
  };
}

/** Routes the two endpoints an answer needs and records what was asked for, so
 * a test can assert on fan-out rather than on rendered output. */
function stubApi(options: { eventPages?: number } = {}) {
  const runRequests: string[] = [];
  const eventRequests: string[] = [];
  const eventPages = options.eventPages ?? 1;
  vi.spyOn(global, "fetch").mockImplementation((input) => {
    const url = new URL(String(input), "http://127.0.0.1:8000");
    const events = /^\/v1\/runs\/([^/]+)\/events$/.exec(url.pathname);
    if (events) {
      const runId = events[1] ?? "";
      eventRequests.push(runId);
      const page = Number(url.searchParams.get("cursor") ?? "0");
      return Promise.resolve(
        jsonResponse({
          items: [
            {
              event_id: `event-${runId}-${page}`,
              run_id: runId,
              step_id: null,
              type: "agent_finished",
              sequence: page + 1,
              occurred_at: "2026-07-26T00:00:00Z",
              payload: { summary: `answer for ${runId}`, details: null },
            },
          ],
          next_cursor: page + 1 < eventPages ? String(page + 1) : null,
        }),
      );
    }
    const run = /^\/v1\/runs\/([^/]+)$/.exec(url.pathname);
    if (run) {
      const runId = run[1] ?? "";
      runRequests.push(runId);
      return Promise.resolve(
        jsonResponse({
          id: runId,
          task_id: "task-1",
          status: "succeeded",
          created_at: "2026-07-26T00:00:00Z",
          failure: null,
        }),
      );
    }
    throw new Error(`unexpected request: ${url.pathname}`);
  });
  return { runRequests, eventRequests };
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useConversationAnswers", () => {
  it("hydrates only the newest window of a long conversation", async () => {
    const api = stubApi();
    const turns = Array.from({ length: 50 }, (_, index) => turn(index));

    const { result } = renderHook(() => useConversationAnswers("c-1", turns), {
      wrapper,
    });
    await waitFor(() =>
      expect(result.current.answers.size).toBe(ANSWER_WINDOW_TURNS),
    );

    // 50 turns must not mean 50 run requests plus an event walk each.
    expect(result.current.visibleTurns).toHaveLength(ANSWER_WINDOW_TURNS);
    expect(new Set(api.runRequests).size).toBe(ANSWER_WINDOW_TURNS);
    expect(result.current.earlierCount).toBe(50 - ANSWER_WINDOW_TURNS);
    // The window holds the newest turns, so a fresh answer is never cut off.
    expect(result.current.visibleTurns[0]?.run_id).toBe(
      `run-${50 - ANSWER_WINDOW_TURNS}`,
    );
    expect(result.current.answers.get("run-49")).toEqual({
      state: "answered",
      summary: "answer for run-49",
      details: null,
    });
  });

  it("reveals earlier turns without re-requesting settled answers", async () => {
    const api = stubApi();
    const turns = Array.from({ length: 50 }, (_, index) => turn(index));

    const { result } = renderHook(() => useConversationAnswers("c-1", turns), {
      wrapper,
    });
    await waitFor(() =>
      expect(result.current.answers.size).toBe(ANSWER_WINDOW_TURNS),
    );
    act(() => result.current.showEarlier());
    await waitFor(() =>
      expect(result.current.visibleTurns).toHaveLength(ANSWER_WINDOW_TURNS * 2),
    );

    // Only the newly revealed turns are fetched; the first window is final and
    // stays cached, so widening costs one request per new turn and no more.
    expect(api.runRequests).toHaveLength(ANSWER_WINDOW_TURNS * 2);
    expect(new Set(api.runRequests).size).toBe(ANSWER_WINDOW_TURNS * 2);
  });

  it("caps the event page walk for a single answer", async () => {
    const api = stubApi({ eventPages: 100 });

    const { result } = renderHook(
      () => useConversationAnswers("c-1", [turn(0)]),
      { wrapper },
    );
    await waitFor(() => expect(result.current.answers.size).toBe(1));

    expect(api.eventRequests).toHaveLength(MAX_ANSWER_EVENT_PAGES);
  });

  it("requests nothing until a conversation exists", () => {
    const api = stubApi();

    const { result } = renderHook(() => useConversationAnswers(null, []), {
      wrapper,
    });

    expect(result.current.visibleTurns).toEqual([]);
    expect(api.runRequests).toEqual([]);
  });
});
