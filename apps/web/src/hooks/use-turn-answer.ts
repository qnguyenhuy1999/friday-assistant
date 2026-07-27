import type { JsonValue, Run, RunEvent } from "@friday/contracts";
export interface TurnAnswer {
  state: "pending" | "awaiting_approval" | "answered" | "failed" | "cancelled";
  summary: string | null;
  details: JsonValue;
}
export const PENDING_TURN_ANSWER: TurnAnswer = {
  state: "pending",
  summary: null,
  details: null,
};
/** Whether an answer can still change. A pending run has not finished, and a
 * run waiting for approval resumes once a human decides, so both must keep
 * being polled; everything else is final and can be cached for good. */
export function isSettledTurnAnswer(answer: TurnAnswer | undefined): boolean {
  return (
    answer !== undefined &&
    answer.state !== "pending" &&
    answer.state !== "awaiting_approval"
  );
}
/** Maps a run (plus its events, once succeeded) onto what the transcript shows.
 * Pure, so the batching layer decides what to fetch and this decides what it
 * means. `events` is only consulted for a succeeded run, whose answer lives in
 * the last `agent_finished` payload. */
export function answerFromRun(
  run: Run | undefined,
  events: readonly RunEvent[] | undefined,
): TurnAnswer {
  if (!run) return PENDING_TURN_ANSWER;
  // Checked before the terminal statuses: a run waiting for approval is not
  // terminal, so gating this behind a terminal check makes it unreachable.
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
  if (run.status !== "succeeded") return PENDING_TURN_ANSWER;
  const payload = [...(events ?? [])]
    .reverse()
    .find((event) => event.type === "agent_finished")?.payload;
  const record =
    payload !== null &&
    payload !== undefined &&
    typeof payload === "object" &&
    !Array.isArray(payload)
      ? (payload as Record<string, JsonValue>)
      : {};
  return {
    state: "answered",
    summary: typeof record.summary === "string" ? record.summary : null,
    details: record.details ?? null,
  };
}
