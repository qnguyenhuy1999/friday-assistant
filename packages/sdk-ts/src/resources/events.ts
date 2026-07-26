import type {
  Page,
  RunEvent,
  RunEventType,
  TaskEvent,
} from "@friday/contracts";
import {
  validateRunEvent,
  validateRunEventPage,
  validateTaskEventPage,
} from "@friday/contracts";
import type { FridayHttpClient } from "../http";
export interface ListEventsParams {
  limit?: number;
  cursor?: string;
}
export interface RunEventStreamOptions {
  EventSourceImpl?: typeof EventSource;
}
const eventTypes: RunEventType[] = [
  "run_created",
  "run_started",
  "run_waiting_for_approval",
  "run_resumed",
  "run_succeeded",
  "run_failed",
  "run_cancelled",
  "step_created",
  "step_started",
  "step_succeeded",
  "step_failed",
  "step_skipped",
  "step_cancelled",
  "approval_requested",
  "approval_resolved",
  "tool_invocation_requested",
  "tool_invocation_started",
  "tool_invocation_succeeded",
  "tool_invocation_failed",
  "tool_invocation_cancelled",
  "artifact_created",
  "agent_finished",
  "memory_context_attached",
  "memory_retrieval_degraded",
  "memory_write_requested",
  "memory_write_committed",
  "memory_write_conflicted",
  "memory_index_marked_stale",
];
export class RunEventStream {
  private readonly source: EventSource;
  private readonly listeners = new Set<(event: RunEvent) => void>();
  private readonly handler = (message: MessageEvent<string>) => {
    const event: unknown = JSON.parse(message.data);
    validateRunEvent(event);
    for (const listener of this.listeners) listener(event as RunEvent);
  };
  constructor(url: string, Source: typeof EventSource) {
    this.source = new Source(url);
    for (const type of eventTypes)
      this.source.addEventListener(type, this.handler as EventListener);
  }
  onEvent(listener: (event: RunEvent) => void) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
  onError(listener: (event: Event) => void) {
    this.source.addEventListener("error", listener);
    return () => this.source.removeEventListener("error", listener);
  }
  close() {
    this.source.close();
  }
}
export class EventsResource {
  constructor(
    private readonly http: FridayHttpClient,
    private readonly baseUrl = "",
  ) {}
  listForRun(id: string, p: ListEventsParams = {}) {
    return this.http.request<Page<RunEvent>>({
      method: "GET",
      path: `/v1/runs/${id}/events`,
      query: { limit: p.limit, cursor: p.cursor },
      validate: validateRunEventPage,
    });
  }
  listForTask(id: string, p: ListEventsParams = {}) {
    return this.http.request<Page<TaskEvent>>({
      method: "GET",
      path: `/v1/tasks/${id}/events`,
      query: { limit: p.limit, cursor: p.cursor },
      validate: validateTaskEventPage,
    });
  }
  streamForRun(id: string, options: RunEventStreamOptions = {}) {
    return new RunEventStream(
      `${this.baseUrl}/v1/runs/${id}/events/stream`,
      options.EventSourceImpl ?? EventSource,
    );
  }
}
