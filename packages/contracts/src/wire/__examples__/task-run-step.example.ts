import type {
  CreateStepBody,
  CreateTaskBody,
  Run,
  RunStep,
  StartRunResponse,
  Task,
} from "../../index";

export const createTaskBodyExample: CreateTaskBody = {
  title: "Ship Phase 14",
  description: "",
};

export const taskExample: Task = {
  id: "8f14e45f-ceea-467e-adde-3f4694a01234",
  title: "Ship Phase 14",
  description: "",
  status: "active",
  created_at: "2026-07-26T00:00:00Z",
  failure: null,
};

export const startRunResponseExample: StartRunResponse = {
  task_id: taskExample.id,
  run_id: "8f14e45f-ceea-467e-adde-3f4694a05678",
};

export const runExample: Run = {
  id: startRunResponseExample.run_id,
  task_id: taskExample.id,
  status: "running",
  created_at: "2026-07-26T00:00:01Z",
  failure: null,
};

export const createStepBodyExample: CreateStepBody = { name: "clone repo" };

export const runStepExample: RunStep = {
  id: "8f14e45f-ceea-467e-adde-3f4694a09abc",
  run_id: runExample.id,
  name: "clone repo",
  position: 0,
  status: "running",
  failure: null,
};
