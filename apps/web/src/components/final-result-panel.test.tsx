import type { Run } from "@friday/contracts";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FinalResultPanel } from "./final-result-panel";

function run(overrides: Partial<Run>): Run {
  return {
    id: "r-1",
    task_id: "t-1",
    status: "running",
    created_at: "x",
    failure: null,
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
