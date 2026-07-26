import type { RunEventType } from "@friday/contracts";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useRunEventStream } from "./use-run-event-stream";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readonly listeners = new Map<string, Set<EventListener>>();
  closed = false;
  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, listener: EventListener): void {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(listener);
  }
  removeEventListener(type: string, listener: EventListener): void {
    this.listeners.get(type)?.delete(listener);
  }
  close(): void {
    this.closed = true;
  }
  emit(type: RunEventType, event: unknown): void {
    for (const listener of this.listeners.get(type) ?? [])
      (listener as (message: MessageEvent) => void)({
        data: JSON.stringify(event),
      } as MessageEvent);
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const event = (
  event_id: string,
  sequence: number,
  type: RunEventType = "run_started",
) => ({
  event_id,
  run_id: "r-1",
  step_id: null,
  type,
  sequence,
  occurred_at: `t-${sequence}`,
  payload: null,
});

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

describe("useRunEventStream", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("backfills existing events then appends live ones without duplicating", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      jsonResponse({ items: [event("e1", 1)], next_cursor: null }),
    );
    const { result } = renderHook(() => useRunEventStream("r-1"), { wrapper });
    await waitFor(() => expect(result.current).toHaveLength(1));

    act(() =>
      FakeEventSource.instances[0]!.emit(
        "step_created",
        event("e2", 2, "step_created"),
      ),
    );
    await waitFor(() => expect(result.current).toHaveLength(2));

    // Re-delivering the same event_id must not grow the timeline.
    act(() =>
      FakeEventSource.instances[0]!.emit(
        "step_created",
        event("e2", 2, "step_created"),
      ),
    );
    expect(result.current.map((e) => e.event_id)).toEqual(["e1", "e2"]);
  });

  it("keeps a live event that arrived before the backfill resolved", async () => {
    // The SSE connection opens synchronously while the backfill GET is still in
    // flight, so a live event can land first; a backfill that replaced state
    // would silently drop it.
    let resolveBackfill: (response: Response) => void = () => {};
    vi.spyOn(global, "fetch").mockReturnValue(
      new Promise<Response>((resolve) => {
        resolveBackfill = resolve;
      }),
    );

    const { result } = renderHook(() => useRunEventStream("r-1"), { wrapper });
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    act(() =>
      FakeEventSource.instances[0]!.emit(
        "step_created",
        event("live", 2, "step_created"),
      ),
    );
    await waitFor(() => expect(result.current).toHaveLength(1));

    act(() =>
      resolveBackfill(
        jsonResponse({ items: [event("backfilled", 1)], next_cursor: null }),
      ),
    );
    await waitFor(() => expect(result.current).toHaveLength(2));
    // Ordered by sequence, not by arrival.
    expect(result.current.map((e) => e.event_id)).toEqual([
      "backfilled",
      "live",
    ]);
  });

  it("closes the stream on unmount", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      jsonResponse({ items: [], next_cursor: null }),
    );
    const { unmount } = renderHook(() => useRunEventStream("r-1"), { wrapper });
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    unmount();
    expect(FakeEventSource.instances[0]!.closed).toBe(true);
  });

  it("stays empty and does not throw when the backfill fails", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      jsonResponse(
        {
          error: { type: "run_not_found", message: "no such run", details: {} },
        },
        404,
      ),
    );
    const { result } = renderHook(() => useRunEventStream("r-1"), { wrapper });
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(result.current).toEqual([]);
  });

  it("invalidates the query that matches each event kind", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      jsonResponse({ items: [], next_cursor: null }),
    );
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    renderHook(() => useRunEventStream("r-1"), {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      ),
    });
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const source = FakeEventSource.instances[0]!;

    invalidate.mockClear();
    act(() => source.emit("step_started", event("s", 1, "step_started")));
    act(() =>
      source.emit(
        "tool_invocation_succeeded",
        event("t", 2, "tool_invocation_succeeded"),
      ),
    );
    act(() =>
      source.emit("artifact_created", event("a", 3, "artifact_created")),
    );
    act(() =>
      source.emit("approval_requested", event("p", 4, "approval_requested")),
    );

    const invalidatedKeys = invalidate.mock.calls.map(
      ([arg]) => (arg as { queryKey: readonly unknown[] }).queryKey[0],
    );
    expect(new Set(invalidatedKeys)).toEqual(
      new Set([
        "run",
        "run-steps",
        "run-tool-invocations",
        "run-artifacts",
        "run-approvals",
      ]),
    );
  });
});
