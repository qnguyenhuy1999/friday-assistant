import type { RunEvent, TaskEvent } from "../../index";

export const runEventExample: RunEvent = {
  event_id: "8f14e45f-ceea-467e-adde-3f4694a0cccc",
  run_id: "8f14e45f-ceea-467e-adde-3f4694a05678",
  step_id: null,
  type: "run_started",
  sequence: 1,
  occurred_at: "2026-07-26T00:00:01Z",
  payload: null,
};

export const taskEventExample: TaskEvent = {
  event_id: "8f14e45f-ceea-467e-adde-3f4694a0dddd",
  task_id: "8f14e45f-ceea-467e-adde-3f4694a01234",
  type: "task_completed",
  sequence: 1,
  occurred_at: "2026-07-26T00:00:05Z",
  payload: null,
};
