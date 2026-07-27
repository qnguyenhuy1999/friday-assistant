import type { ConversationTurn, Run } from "@friday/contracts";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useMemo, useRef } from "react";
import { friday } from "../friday-client";
import {
  answerFromRun,
  isSettledTurnAnswer,
  PENDING_TURN_ANSWER,
  type TurnAnswer,
} from "./use-turn-answer";

/** Answer loads in flight at once, so a freshly revealed page cannot burst. */
export const ANSWER_CONCURRENCY = 6;
/** `agent_finished` is written at the tail of a succeeded run's log, so the walk
 * is short in practice; the cap stops a pathological run from paging forever. */
export const ANSWER_POLL_MS = 5_000;

const EMPTY_ANSWERS: ReadonlyMap<string, TurnAnswer> = new Map();

export const conversationAnswersQueryKey = (
  conversationId: string,
  runIds: readonly string[],
) => ["conversation-answers", conversationId, runIds] as const;

/** Given a run id, resolve the effective run by walking the execution chain.
 * If the root run is terminal with a retryable failure, follow retries to the
 * latest live or terminal run. Returns the effective run id + its status. */
export async function resolveEffectiveRun(
  runId: string,
): Promise<{ effectiveRunId: string; run: Run }> {
  const run = await friday.runs.get(runId);
  if (run.status === "failed" && run.failure?.retryable && run.execution_id) {
    const chain = await friday.runs.listByExecution(runId);
    if (chain.items.length > 1) {
      for (const candidate of [...chain.items].reverse()) {
        if (candidate.status !== "failed" && candidate.status !== "cancelled") {
          return { effectiveRunId: candidate.id, run: candidate };
        }
      }
      return {
        effectiveRunId: chain.items.at(-1)!.id,
        run: chain.items.at(-1)!,
      };
    }
  }
  return { effectiveRunId: runId, run };
}

async function loadAnswer(runId: string): Promise<TurnAnswer> {
  const { run } = await resolveEffectiveRun(runId);
  const result =
    run.status === "succeeded"
      ? await friday.runs.getResult(run.id)
      : undefined;
  return answerFromRun(run, result);
}

async function mapWithConcurrency<T, R>(
  items: readonly T[],
  limit: number,
  work: (item: T) => Promise<R>,
): Promise<(R | undefined)[]> {
  const results = new Array<R | undefined>(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const index = next;
      next += 1;
      const item = items[index];
      if (item !== undefined) results[index] = await work(item);
    }
  };
  await Promise.all(
    Array.from({ length: Math.min(limit, items.length) }, worker),
  );
  return results;
}

/** Loads answers for the loaded turns as one bounded query.
 *
 * Replaces a per-turn hook that issued its own run query and walked every event
 * page: N turns meant N polling queries plus an unbounded page walk each, so
 * reopening a long conversation fanned out into thousands of requests. Here the
 * loaded transcript is one query with one poll timer, capped concurrency, and
 * final answers cached so a poll only re-requests what can still change.
 *
 * What bounds the total is the transcript's own cursor paging: only the pages
 * the user has actually asked for are loaded, so the fan-out grows with what is
 * on screen rather than with the age of the conversation.
 *
 * A single request for the whole page would need a batch endpoint the API does
 * not expose today (`GET /v1/runs/{id}` and `/v1/runs/{id}/result` are both
 * single-run); this bounds the fan-out without touching the API. */
export function useConversationAnswers(
  conversationId: string | null,
  turns: ConversationTurn[],
): ReadonlyMap<string, TurnAnswer> {
  const runIds = useMemo(() => turns.map((turn) => turn.run_id), [turns]);
  // Answers that can no longer change, kept for the life of the page so a poll
  // re-requests only the runs still in flight instead of the whole window.
  const settled = useRef(new Map<string, TurnAnswer>());
  const { data } = useQuery({
    queryKey: conversationAnswersQueryKey(conversationId ?? "", runIds),
    enabled: conversationId !== null && runIds.length > 0,
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const missing = runIds.filter((id) => !settled.current.has(id));
      const loaded = await mapWithConcurrency(
        missing,
        ANSWER_CONCURRENCY,
        // One unreachable run degrades to pending — and so is retried on the
        // next poll — rather than emptying the whole transcript.
        (id: string) => loadAnswer(id).catch(() => PENDING_TURN_ANSWER),
      );
      const answers = new Map(settled.current);
      missing.forEach((id, index) => {
        const answer = loaded[index] ?? PENDING_TURN_ANSWER;
        answers.set(id, answer);
        if (isSettledTurnAnswer(answer)) settled.current.set(id, answer);
      });
      return answers;
    },
    refetchInterval: ({ state }) =>
      runIds.some((id) => !isSettledTurnAnswer(state.data?.get(id)))
        ? ANSWER_POLL_MS
        : false,
  });
  return data ?? EMPTY_ANSWERS;
}
