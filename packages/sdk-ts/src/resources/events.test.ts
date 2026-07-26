import { describe, expect, it, vi } from "vitest";
import { EventsResource } from "./events";
import { FridayHttpClient } from "../http";
import {
  WireFormatError,
  validateRunEventPage,
  validateTaskEventPage,
} from "@friday/contracts";
class Source {
  static latest: Source;
  listeners = new Map<string, EventListener>();
  closed = false;
  constructor(readonly url: string) {
    Source.latest = this;
  }
  addEventListener(type: string, fn: EventListener) {
    this.listeners.set(type, fn);
  }
  removeEventListener() {}
  close() {
    this.closed = true;
  }
  emit(type: string) {
    this.listeners.get(type)?.({
      data: JSON.stringify({
        event_id: "e",
        run_id: "r",
        step_id: null,
        type,
        sequence: 1,
        occurred_at: "x",
        payload: null,
      }),
    } as unknown as Event);
  }
}
describe("EventsResource", () => {
  it("uses named SSE events and closes streams", () => {
    const http = { request: vi.fn() } as unknown as FridayHttpClient;
    const stream = new EventsResource(http, "http://api.test").streamForRun(
      "r",
      { EventSourceImpl: Source as unknown as typeof EventSource },
    );
    const received: string[] = [];
    stream.onEvent((event) => received.push(event.type));
    Source.latest.emit("run_started");
    expect(Source.latest.url).toBe("http://api.test/v1/runs/r/events/stream");
    expect(received).toEqual(["run_started"]);
    stream.close();
    expect(Source.latest.closed).toBe(true);
  });
  it("delivers every RunEventType, not only a default message event", () => {
    const http = { request: vi.fn() } as unknown as FridayHttpClient;
    const stream = new EventsResource(http, "http://api.test").streamForRun(
      "r",
      { EventSourceImpl: Source as unknown as typeof EventSource },
    );
    const received: string[] = [];
    stream.onEvent((event) => received.push(event.type));
    Source.latest.emit("approval_requested");
    Source.latest.emit("memory_index_marked_stale");
    Source.latest.emit("agent_finished");
    expect(received).toEqual([
      "approval_requested",
      "memory_index_marked_stale",
      "agent_finished",
    ]);
    // The API never emits an unnamed `message` frame; nothing listens for one.
    expect(Source.latest.listeners.has("message")).toBe(false);
    stream.close();
  });
  it("onEvent() returns a working unsubscribe", () => {
    const http = { request: vi.fn() } as unknown as FridayHttpClient;
    const stream = new EventsResource(http, "http://api.test").streamForRun(
      "r",
      { EventSourceImpl: Source as unknown as typeof EventSource },
    );
    const received: string[] = [];
    const off = stream.onEvent((event) => received.push(event.type));
    off();
    Source.latest.emit("run_started");
    expect(received).toEqual([]);
    stream.close();
  });
  it("lists run and task events with paging params", async () => {
    const request = vi.fn().mockResolvedValue({});
    const events = new EventsResource(
      { request } as unknown as FridayHttpClient,
      "http://api.test",
    );
    await events.listForRun("r-1", { limit: 10 });
    await events.listForTask("t-1");
    expect(request).toHaveBeenNthCalledWith(1, {
      method: "GET",
      path: "/v1/runs/r-1/events",
      query: { limit: 10, cursor: undefined },
      validate: validateRunEventPage,
    });
    expect(request).toHaveBeenNthCalledWith(2, {
      method: "GET",
      path: "/v1/tasks/t-1/events",
      query: { limit: undefined, cursor: undefined },
      validate: validateTaskEventPage,
    });
  });

  it("validates a task event page through the real HTTP client", async () => {
    const response = (body: unknown) => new Response(JSON.stringify(body));
    const events = new EventsResource(
      new FridayHttpClient({
        baseUrl: "http://api.test",
        fetchImpl: vi.fn().mockResolvedValue(
          response({
            items: [
              {
                event_id: "e-1",
                task_id: "t-1",
                type: "task_completed",
                sequence: 1,
                occurred_at: "now",
                payload: null,
              },
            ],
            next_cursor: null,
          }),
        ),
      }),
    );
    await expect(events.listForTask("t-1")).resolves.toMatchObject({
      items: [{ task_id: "t-1" }],
    });
    const invalid = new EventsResource(
      new FridayHttpClient({
        baseUrl: "http://api.test",
        fetchImpl: vi.fn().mockResolvedValue(
          response({
            items: [{ event_id: "e-1", task_id: "t-1", type: "run_started" }],
            next_cursor: null,
          }),
        ),
      }),
    );
    await expect(invalid.listForTask("t-1")).rejects.toBeInstanceOf(
      WireFormatError,
    );
  });
});
