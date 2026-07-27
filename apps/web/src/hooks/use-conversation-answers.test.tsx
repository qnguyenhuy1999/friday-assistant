import type { ConversationTurn } from "@friday/contracts";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { friday } from "../friday-client";
import { useConversationAnswers } from "./use-conversation-answers";

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
function stubApi() {
  const runRequests: string[] = [];
  const resultRequests: string[] = [];
  vi.spyOn(global, "fetch").mockImplementation((input) => {
    const url = new URL(String(input), "http://127.0.0.1:8000");
    const result = /^\/v1\/runs\/([^/]+)\/result$/.exec(url.pathname);
    if (result) {
      const runId = result[1] ?? "";
      resultRequests.push(runId);
      return Promise.resolve(
        jsonResponse({
          summary: `answer for ${runId}`,
          details: null,
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
  return { runRequests, resultRequests };
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
  it("validates the durable result projection", async () => {
    stubApi();
    await expect(friday.runs.getResult("run-0")).resolves.toEqual({
      summary: "answer for run-0",
      details: null,
    });
  });
  it("hydrates an answer for every loaded turn exactly once", async () => {
    const api = stubApi();
    const turns = Array.from({ length: 25 }, (_, index) => turn(index));

    const { result } = renderHook(() => useConversationAnswers("c-1", turns), {
      wrapper,
    });
    await waitFor(() =>
      expect(result.current.get("run-24")?.state).toBe("answered"),
    );

    // One run request per loaded turn and no event walk behind it. What keeps
    // this bounded on a long conversation is that only the pages the user
    // asked for are ever loaded.
    expect(new Set(api.runRequests).size).toBe(25);
    expect(api.runRequests).toHaveLength(25);
    expect(result.current.get("run-24")).toEqual({
      state: "answered",
      summary: "answer for run-24",
      details: null,
    });
  });

  it("does not re-request settled answers when earlier turns are revealed", async () => {
    const api = stubApi();
    const newest = Array.from({ length: 5 }, (_, index) => turn(index + 5));

    const { result, rerender } = renderHook(
      ({ turns }: { turns: ConversationTurn[] }) =>
        useConversationAnswers("c-1", turns),
      { wrapper, initialProps: { turns: newest } },
    );
    await waitFor(() =>
      expect(result.current.get("run-9")?.state).toBe("answered"),
    );

    // Fetching an older page prepends turns, exactly as the transcript does.
    const older = Array.from({ length: 5 }, (_, index) => turn(index));
    rerender({ turns: [...older, ...newest] });
    await waitFor(() =>
      expect(result.current.get("run-0")?.state).toBe("answered"),
    );

    // Answers already final stay cached, so revealing history costs one request
    // per newly revealed turn and nothing for what was already on screen.
    expect(api.runRequests).toHaveLength(10);
    expect(new Set(api.runRequests).size).toBe(10);
  });

  it("uses one durable projection request instead of walking event pages", async () => {
    const api = stubApi();

    const { result } = renderHook(
      () => useConversationAnswers("c-1", [turn(0)]),
      { wrapper },
    );
    await waitFor(() =>
      expect(result.current.get("run-0")?.state).toBe("answered"),
    );

    expect(api.resultRequests).toEqual(["run-0"]);
  });

  it("requests nothing until a conversation exists", () => {
    const api = stubApi();

    const { result } = renderHook(() => useConversationAnswers(null, []), {
      wrapper,
    });

    expect(result.current.size).toBe(0);
    expect(api.runRequests).toEqual([]);
  });
});
