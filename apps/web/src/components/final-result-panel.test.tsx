import type { Run } from "@friday/contracts";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FinalResultPanel } from "./final-result-panel";
import type { RunEvent } from "@friday/contracts";

function run(overrides: Partial<Run>): Run {
  return {
    id: "r-1",
    task_id: "t-1",
    status: "running",
    created_at: "x",
    failure: null,
    execution_id: "exec-1",
    ...overrides,
  };
}

describe("FinalResultPanel", () => {
  it("renders nothing while the run is not terminal", () => {
    const { container } = render(
      <FinalResultPanel run={run({ status: "running" })} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("announces a succeeded run", () => {
    render(<FinalResultPanel run={run({ status: "succeeded" })} />);
    expect(screen.getByRole("status")).toHaveTextContent("Run succeeded.");
  });

  it("shows the final agent answer persisted in the event history", () => {
    const events: RunEvent[] = [
      {
        event_id: "e-1",
        run_id: "r-1",
        step_id: null,
        type: "agent_finished",
        sequence: 4,
        occurred_at: "x",
        payload: { summary: "Shipped.", details: "All checks passed." },
      },
    ];
    render(
      <FinalResultPanel run={run({ status: "succeeded" })} events={events} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Final answer");
    expect(screen.getByRole("status")).toHaveTextContent("Shipped.");
    expect(screen.getByRole("status")).toHaveTextContent("All checks passed.");
  });

  it("announces a cancelled run", () => {
    render(<FinalResultPanel run={run({ status: "cancelled" })} />);
    expect(screen.getByRole("status")).toHaveTextContent("Run was cancelled.");
  });

  it("shows the full Failure detail for a failed run", () => {
    render(
      <FinalResultPanel
        run={run({
          status: "failed",
          failure: {
            code: "tool_timeout",
            message: "the shell command timed out",
            retryable: true,
            cause: "timeout",
            details: null,
          },
        })}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Run failed: the shell command timed out");
    expect(alert).toHaveTextContent("tool_timeout");
    expect(alert).toHaveTextContent("timeout");
    expect(alert).toHaveTextContent("yes");
  });

  it("still reports a failed run that carries no Failure payload", () => {
    render(<FinalResultPanel run={run({ status: "failed" })} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Run failed.");
  });
});
