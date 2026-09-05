import { describe, expect, it } from "vitest";
import {
  calculateLaunchReadiness,
  type LaunchReadinessInput,
} from "./task-launch-readiness";

const readyInput: LaunchReadinessInput = {
  taskStatus: "active",
  bindingsLoading: false,
  bindingLoadError: false,
  inconsistent: false,
  targetDetailsLoading: false,
  targetDetailsError: false,
  archivedAgent: false,
  archivedWorkflow: false,
  mutationPending: false,
  startRunPending: false,
  skillBindingsLoading: false,
  skillBindingLoadError: false,
  skillDetailsLoading: false,
  skillDetailsLoadError: false,
  unresolvableSkillReason: null,
  skillMutationPending: false,
  unsavedSkillDraft: false,
};

describe("calculateLaunchReadiness", () => {
  it.each([
    ["skill bindings loading", { skillBindingsLoading: true }],
    ["skill binding load failure", { skillBindingLoadError: true }],
    ["bound Skill details loading", { skillDetailsLoading: true }],
    ["bound Skill detail failure", { skillDetailsLoadError: true }],
    [
      "unresolvable persisted Skill",
      { unresolvableSkillReason: "The bound Skill is not resolvable." },
    ],
    ["Skill mutation pending", { skillMutationPending: true }],
    ["unsaved Skill draft", { unsavedSkillDraft: true }],
  ] as const)("blocks Start Run for %s", (_label, change) => {
    const readiness = calculateLaunchReadiness({ ...readyInput, ...change });
    expect(readiness.canStartRun).toBe(false);
  });

  it("preserves existing Agent/Workflow readiness rules when Skills are ready", () => {
    expect(
      calculateLaunchReadiness({ ...readyInput, archivedAgent: true })
        .unavailableReason,
    ).toContain("bound Agent is archived");
    expect(
      calculateLaunchReadiness({ ...readyInput, archivedWorkflow: true })
        .unavailableReason,
    ).toContain("bound Workflow is archived");
    expect(
      calculateLaunchReadiness({ ...readyInput, inconsistent: true })
        .unavailableReason,
    ).toContain("Both Agent and Workflow bindings are present");
  });

  it("allows a Task with no Skills once every persisted dependency is verified", () => {
    expect(calculateLaunchReadiness(readyInput)).toEqual({
      canStartRun: true,
      unavailableReason: null,
    });
  });
});
