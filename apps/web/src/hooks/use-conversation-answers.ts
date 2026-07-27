import type { ConversationTurn } from "@friday/contracts";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useCallback, useMemo, useRef, useState } from "react";
import { friday } from "../friday-client";
import {
  answerFromRun,
  isSettledTurnAnswer,
  PENDING_TURN_ANSWER,
  type TurnAnswer,
} from "./use-turn-answer";

/** Turns that hydrate an answer at once. Each hydrated turn costs a run request
 * plus, once succeeded, an event request, and a conversation grows without
 * bound — so this window is what stops a long history from fanning out into
 * thousands of requests on load. Older turns stay reachable via `showEarlier`. */
export const ANSWER_WINDOW_TURNS = 20;
/** Answer loads in flight at once, so widening the window cannot burst. */
export const ANSWER_CONCURRENCY = 6;
/** `agent_finished` is written at the tail of a succeeded run's log, so the walk
 * is short in practice; the cap stops a pathological run from paging forever. */
export const ANSWER_POLL_MS = 5_000;

const EMPTY_ANSWERS: ReadonlyMap<string, TurnAnswer> = new Map();

export const conversationAnswersQueryKey = (
  conversationId: string,
  runIds: readonly string[],
) => ["conversation-answers", conversationId, runIds] as const;

async function loadAnswer(runId: string): Promise<TurnAnswer> {
  const run = await friday.runs.get(runId);
  const result =
    run.status === "succeeded" ? await friday.runs.getResult(runId) : undefined;
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

export interface ConversationAnswers {
  /** The turns to render — the newest `windowSize`, so a new turn is always in. */
  visibleTurns: ConversationTurn[];
  answers: ReadonlyMap<string, TurnAnswer>;
  /** Turns held back by the window, for an affordance to reveal them. */
  earlierCount: number;
  showEarlier(): void;
}

/** Loads answers for the visible turns as one bounded query.
 *
 * Replaces a per-turn hook that issued its own run query and walked every event
 * page: N turns meant N polling queries plus an unbounded page walk each, so
 * reopening a long conversation fanned out into thousands of requests. Here the
 * whole window is one query with one poll timer, capped concurrency, capped
 * paging, and final answers cached so a poll only re-requests what can still
 * change.
 *
 * A single request for the whole window would need a batch endpoint the API
 * does not expose today (`GET /v1/runs/{id}` and `/v1/runs/{id}/events` are
 * both single-run); this bounds the fan-out without touching the API. */
export function useConversationAnswers(
  conversationId: string | null,
  turns: ConversationTurn[],
): ConversationAnswers {
  const [windowSize, setWindowSize] = useState(ANSWER_WINDOW_TURNS);
  const visibleTurns = useMemo(
    () =>
      turns.length > windowSize
        ? turns.slice(turns.length - windowSize)
        : turns,
    [turns, windowSize],
  );
  const runIds = useMemo(
    () => visibleTurns.map((turn) => turn.run_id),
    [visibleTurns],
  );
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
  const showEarlier = useCallback(
    () => setWindowSize((size) => size + ANSWER_WINDOW_TURNS),
    [],
  );
  return {
    visibleTurns,
    answers: data ?? EMPTY_ANSWERS,
    earlierCount: turns.length - visibleTurns.length,
    showEarlier,
  };
}
