import type { Task } from "@friday/contracts";

export function taskCanStartRun(status: Task["status"]): boolean {
  return status === "pending" || status === "active";
}

export interface LaunchReadinessInput {
  taskStatus: Task["status"];
  bindingsLoading: boolean;
  bindingLoadError: boolean;
  inconsistent: boolean;
  targetDetailsLoading: boolean;
  targetDetailsError: boolean;
  archivedAgent: boolean;
  archivedWorkflow: boolean;
  mutationPending: boolean;
  startRunPending: boolean;
}

export interface LaunchReadiness {
  canStartRun: boolean;
  unavailableReason: string | null;
}

export function calculateLaunchReadiness(
  input: LaunchReadinessInput,
): LaunchReadiness {
  if (!taskCanStartRun(input.taskStatus))
    return {
      canStartRun: false,
      unavailableReason: `This Task is ${input.taskStatus} and cannot start another Run.`,
    };
  if (input.inconsistent)
    return {
      canStartRun: false,
      unavailableReason:
        "Task execution-target state is inconsistent. Both Agent and Workflow bindings are present.",
    };
  if (input.bindingsLoading || input.bindingLoadError)
    return {
      canStartRun: false,
      unavailableReason:
        "Task execution-target bindings cannot be verified right now.",
    };
  if (input.targetDetailsLoading || input.targetDetailsError)
    return {
      canStartRun: false,
      unavailableReason:
        "The bound execution target cannot be verified right now.",
    };
  if (input.archivedAgent)
    return {
      canStartRun: false,
      unavailableReason:
        "The bound Agent is archived and cannot be reactivated through the supported lifecycle. Clear or replace the Task binding before starting another Run.",
    };
  if (input.archivedWorkflow)
    return {
      canStartRun: false,
      unavailableReason:
        "The bound Workflow is archived and cannot be reactivated through the supported lifecycle. Clear or replace the Task binding before starting another Run.",
    };
  if (input.mutationPending || input.startRunPending)
    return {
      canStartRun: false,
      unavailableReason: "A Task execution change is still in progress.",
    };
  return { canStartRun: true, unavailableReason: null };
}
