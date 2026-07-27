import type { Run, RunResult, RunStatus } from "@friday/contracts";
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

  it("reads the answer from the durable result projection", () => {
    const result: RunResult = { summary: "final", details: { count: 2 } };

    expect(answerFromRun(run("succeeded"), result)).toEqual({
      state: "answered",
      summary: "final",
      details: { count: 2 },
    });
  });

  it("answers with no summary when the succeeded run never reported one", () => {
    expect(answerFromRun(run("succeeded"), undefined)).toEqual({
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
    expect(answerFromRun(run("failed", failure), undefined)).toEqual({
      state: "failed",
      summary: "it broke",
      details: null,
    });
    expect(answerFromRun(run("cancelled"), undefined)).toEqual({
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
