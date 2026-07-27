import type { JsonValue, Run, RunResult } from "@friday/contracts";
export interface TurnAnswer {
  /** Retry descendants own the current result and approval state. */
  runId: string | null;
  state: "pending" | "awaiting_approval" | "answered" | "failed" | "cancelled";
  summary: string | null;
  details: JsonValue;
}
export const PENDING_TURN_ANSWER: TurnAnswer = {
  runId: null,
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
 * means. A succeeded run reads a dedicated durable result projection rather
 * than paging its unbounded event log. */
export function answerFromRun(
  run: Run | undefined,
  result: RunResult | undefined,
): TurnAnswer {
  if (!run) return PENDING_TURN_ANSWER;
  // Checked before the terminal statuses: a run waiting for approval is not
  // terminal, so gating this behind a terminal check makes it unreachable.
  if (run.status === "waiting_for_approval")
    return {
      runId: run.id,
      state: "awaiting_approval",
      summary: null,
      details: null,
    };
  if (run.status === "failed")
    return {
      runId: run.id,
      state: "failed",
      summary: run.failure?.message ?? null,
      details: null,
    };
  if (run.status === "cancelled")
    return { runId: run.id, state: "cancelled", summary: null, details: null };
  if (run.status !== "succeeded") return PENDING_TURN_ANSWER;
  return {
    runId: run.id,
    state: "answered",
    summary: result?.summary ?? null,
    details: result?.details ?? null,
  };
}
