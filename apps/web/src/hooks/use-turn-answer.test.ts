import type { Run, RunEvent, RunStatus } from "@friday/contracts";
import { describe, expect, it } from "vitest";
import {
  answerFromRun,
  isSettledTurnAnswer,
  PENDING_TURN_ANSWER,
} from "./use-turn-answer";

function run(status: RunStatus, failure: Run["failure"] = null): Run {
  return {
    id: "run-1",
    task_id: "task-1",
    status,
    created_at: "2026-07-26T00:00:00Z",
    failure,
  };
}

function event(sequence: number, payload: RunEvent["payload"]): RunEvent {
  return {
    event_id: `event-${sequence}`,
    run_id: "run-1",
    step_id: null,
    type: "agent_finished",
    sequence,
    occurred_at: "2026-07-26T00:00:00Z",
    payload,
  };
}

describe("answerFromRun", () => {
  it("surfaces an approval wait, which is not a terminal status", () => {
    expect(answerFromRun(run("waiting_for_approval"), undefined)).toEqual({
      state: "awaiting_approval",
      summary: null,
      details: null,
    });
  });

  it("stays pending while the run is still moving", () => {
    expect(answerFromRun(run("queued"), undefined)).toEqual(
      PENDING_TURN_ANSWER,
    );
    expect(answerFromRun(run("running"), undefined)).toEqual(
      PENDING_TURN_ANSWER,
    );
    expect(answerFromRun(undefined, undefined)).toEqual(PENDING_TURN_ANSWER);
  });

  it("reads the answer from the last agent_finished event", () => {
    const events = [
      event(1, { summary: "first", details: null }),
      event(2, { summary: "final", details: { count: 2 } }),
    ];

    expect(answerFromRun(run("succeeded"), events)).toEqual({
      state: "answered",
      summary: "final",
      details: { count: 2 },
    });
  });

  it("answers with no summary when the succeeded run never reported one", () => {
    expect(answerFromRun(run("succeeded"), [])).toEqual({
      state: "answered",
      summary: null,
      details: null,
    });
  });

  it("reports a failure message and a cancellation", () => {
    const failure = {
      code: "boom",
      message: "it broke",
      retryable: false,
      cause: "runtime",
      details: null,
    } as const;
    expect(answerFromRun(run("failed", failure), [])).toEqual({
      state: "failed",
      summary: "it broke",
      details: null,
    });
    expect(answerFromRun(run("cancelled"), [])).toEqual({
      state: "cancelled",
      summary: null,
      details: null,
    });
  });
});

describe("isSettledTurnAnswer", () => {
  it("keeps polling anything that can still change", () => {
    expect(isSettledTurnAnswer(undefined)).toBe(false);
    expect(isSettledTurnAnswer(PENDING_TURN_ANSWER)).toBe(false);
    expect(
      isSettledTurnAnswer({
        state: "awaiting_approval",
        summary: null,
        details: null,
      }),
    ).toBe(false);
  });

  it("treats a finished answer as final", () => {
    expect(
      isSettledTurnAnswer({ state: "answered", summary: "hi", details: null }),
    ).toBe(true);
    expect(
      isSettledTurnAnswer({ state: "failed", summary: null, details: null }),
    ).toBe(true);
    expect(
      isSettledTurnAnswer({ state: "cancelled", summary: null, details: null }),
    ).toBe(true);
  });
});
