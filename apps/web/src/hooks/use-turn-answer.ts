import type { JsonValue } from "@friday/contracts";
import { useQuery } from "@tanstack/react-query";
import { friday } from "../friday-client";
import { isTerminalRunStatus, useRun } from "./use-run";
import { loadAllPages } from "./use-all-pages";
export interface TurnAnswer {
  state: "pending" | "awaiting_approval" | "answered" | "failed" | "cancelled";
  summary: string | null;
  details: JsonValue;
}
export function useTurnAnswer(runId: string): TurnAnswer {
  const { data: run } = useRun(runId);
  const { data: events } = useQuery({
    queryKey: ["turn-answer", runId],
    enabled: Boolean(
      run && isTerminalRunStatus(run.status) && run.status === "succeeded",
    ),
    queryFn: () =>
      loadAllPages((cursor) =>
        friday.events.listForRun(runId, { limit: 100, cursor }),
      ),
  });
  if (!run || !isTerminalRunStatus(run.status))
    return { state: "pending", summary: null, details: null };
  if (run.status === "waiting_for_approval")
    return { state: "awaiting_approval", summary: null, details: null };
  if (run.status === "failed")
    return {
      state: "failed",
      summary: run.failure?.message ?? null,
      details: null,
    };
  if (run.status === "cancelled")
    return { state: "cancelled", summary: null, details: null };
  const payload = [...(events?.items ?? [])]
    .reverse()
    .find((event) => event.type === "agent_finished")?.payload;
  const record =
    payload !== null && typeof payload === "object" && !Array.isArray(payload)
      ? (payload as Record<string, JsonValue>)
      : {};
  return {
    state: "answered",
    summary: typeof record.summary === "string" ? record.summary : null,
    details: record.details ?? null,
  };
}
