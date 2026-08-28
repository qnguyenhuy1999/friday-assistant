import type { WorkflowEdgeInput, WorkflowNodeInput } from "@friday/contracts";
import { describe, expect, it } from "vitest";
import {
  parseWorkflowInputPayload,
  validateWorkflowDraft,
} from "./workflow-draft";

function node(nodeKey: string): WorkflowNodeInput {
  return {
    node_key: nodeKey,
    target_agent_id: "agent-1",
    objective: `Objective for ${nodeKey}`,
    input_payload: { nodeKey },
    expected_output_contract: "A structured result",
  };
}

describe("workflow draft validation", () => {
  it("accepts a valid DAG", () => {
    const edges: WorkflowEdgeInput[] = [{ from: "analyze", to: "implement" }];
    expect(
      validateWorkflowDraft([node("analyze"), node("implement")], edges),
    ).toBeNull();
  });

  it("rejects a zero-node revision", () => {
    expect(validateWorkflowDraft([], [])).toBe(
      "Add at least one node before creating a revision.",
    );
  });

  it("rejects duplicate node keys", () => {
    expect(validateWorkflowDraft([node("analyze"), node("analyze")], [])).toBe(
      "Node keys must be unique.",
    );
  });

  it("rejects missing edge endpoints", () => {
    expect(
      validateWorkflowDraft(
        [node("analyze")],
        [{ from: "analyze", to: "missing" }],
      ),
    ).toBe("Edges must reference existing node keys.");
  });

  it("rejects self-edges", () => {
    expect(
      validateWorkflowDraft(
        [node("analyze")],
        [{ from: "analyze", to: "analyze" }],
      ),
    ).toBe("An edge cannot point to the same node.");
  });

  it("rejects duplicate edges", () => {
    const edge = { from: "analyze", to: "implement" };
    expect(
      validateWorkflowDraft([node("analyze"), node("implement")], [edge, edge]),
    ).toBe("Duplicate edges are not allowed.");
  });

  it("rejects cycles", () => {
    expect(
      validateWorkflowDraft(
        [node("analyze"), node("implement")],
        [
          { from: "analyze", to: "implement" },
          { from: "implement", to: "analyze" },
        ],
      ),
    ).toBe("The workflow graph cannot contain cycles.");
  });

  it("does not coerce malformed input JSON", () => {
    expect(parseWorkflowInputPayload("not-json")).toEqual({
      error: "Input payload must be valid JSON.",
    });
    expect(parseWorkflowInputPayload("null")).toEqual({ value: null });
  });
});
