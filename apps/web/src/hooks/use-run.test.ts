import type { Run, RunStatus } from "@friday/contracts";
import { describe, expect, it } from "vitest";
import { isTerminalRunStatus, runRefetchIntervalMs } from "./use-run";

function run(status: RunStatus): Run {
  return {
    id: "r-1",
    task_id: "t-1",
    status,
    created_at: "2026-07-26T00:00:00Z",
    failure: null,
  };
}

describe("isTerminalRunStatus", () => {
  it("treats succeeded, failed, and cancelled as terminal", () => {
    expect(isTerminalRunStatus("succeeded")).toBe(true);
    expect(isTerminalRunStatus("failed")).toBe(true);
    expect(isTerminalRunStatus("cancelled")).toBe(true);
  });

  it("treats queued, running, and waiting_for_approval as non-terminal", () => {
    expect(isTerminalRunStatus("queued")).toBe(false);
    expect(isTerminalRunStatus("running")).toBe(false);
    expect(isTerminalRunStatus("waiting_for_approval")).toBe(false);
  });
});

describe("runRefetchIntervalMs", () => {
  it("polls while the run is non-terminal", () => {
    expect(runRefetchIntervalMs(run("running"))).toBe(5000);
    expect(runRefetchIntervalMs(run("waiting_for_approval"))).toBe(5000);
  });

  it("stops polling at every terminal status", () => {
    expect(runRefetchIntervalMs(run("succeeded"))).toBe(false);
    expect(runRefetchIntervalMs(run("failed"))).toBe(false);
    expect(runRefetchIntervalMs(run("cancelled"))).toBe(false);
  });

  it("keeps polling while no run has loaded yet", () => {
    expect(runRefetchIntervalMs(undefined)).toBe(5000);
  });
});
