import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SkillEvaluationRunDetailPage } from "./skill-evaluation-run-detail-page";

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const frozenCases = [
  {
    id: "case-a",
    position: 1,
    input: "Frozen A",
    expected_properties: { value: "SAFE" },
    grading_kind: "exact_match",
  },
  {
    id: "case-b",
    position: 2,
    input: "Frozen B",
    expected_properties: { values: ["DANGEROUS"] },
    grading_kind: "contains_none",
  },
];

function run(overrides: Record<string, unknown> = {}) {
  return {
    id: "eval-run-1",
    suite_id: "suite-1",
    skill_id: "skill-1",
    revision_id: "revision-1",
    proposal_id: null,
    status: "succeeded",
    aggregate_result: { case_count: 2, passed: 2, score: 1 },
    runtime_fingerprint: "d".repeat(64),
    target_content_sha256: "a".repeat(64),
    runtime_metadata: {
      evaluator_version: "deterministic-v1",
      adapter: "code-owned",
    },
    suite_snapshot: { cases: frozenCases },
    case_results: [
      {
        evaluation_run_id: "eval-run-1",
        case_id: "case-b",
        status: "succeeded",
        score: 1,
        reason_code: null,
        bounded_details: "passed B",
        output_sha256: "b".repeat(64),
      },
      {
        evaluation_run_id: "eval-run-1",
        case_id: "case-a",
        status: "succeeded",
        score: 1,
        reason_code: null,
        bounded_details: "passed A",
        output_sha256: "c".repeat(64),
      },
    ],
    ...overrides,
  };
}

function renderPage(value: unknown = run(), onBackToSkill = vi.fn()) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <SkillEvaluationRunDetailPage
        runId="eval-run-1"
        onBackToSkill={onBackToSkill}
      />
    </QueryClientProvider>,
  );
  return { onBackToSkill, value };
}

describe("SkillEvaluationRunDetailPage", () => {
  afterEach(() => vi.restoreAllMocks());

  it("fetches one exact run and renders immutable target, snapshot, metadata, and results in frozen order", async () => {
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValue(response(run()));
    const { onBackToSkill } = renderPage();
    expect(
      await screen.findByRole("heading", { name: "Evaluation Run Detail" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Persisted Skill revision")).toBeInTheDocument();
    expect(screen.getByText("revision-1")).toBeInTheDocument();
    expect(screen.getByText("d".repeat(64))).toBeInTheDocument();
    expect(screen.getByText(/deterministic-v1/)).toBeInTheDocument();
    expect(screen.getByText("Frozen A")).toBeInTheDocument();
    expect(screen.getByText(/"case-a"/)).toBeInTheDocument();

    const resultItems = within(
      screen.getByRole("list", { name: "Frozen evaluation case results" }),
    ).getAllByRole("listitem");
    expect(resultItems).toHaveLength(2);
    expect(resultItems[0]).toHaveTextContent("Frozen A");
    expect(resultItems[1]).toHaveTextContent("Frozen B");
    expect(screen.queryByLabelText("Supplied output")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      "/v1/skills/evaluation-runs/eval-run-1",
    );

    await screen.findByRole("button", { name: "Back to Skill" });
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Back to Skill" }));
    expect(onBackToSkill).toHaveBeenCalledWith("skill-1");
  });

  it("renders historical proposal targets as inspection-only", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      response(run({ revision_id: null, proposal_id: "proposal-1" })),
    );
    renderPage(run({ revision_id: null, proposal_id: "proposal-1" }));
    expect(
      await screen.findByText(
        "Improvement proposal candidate (inspection only)",
      ),
    ).toBeInTheDocument();
    expect(screen.getAllByText("proposal-1")).toHaveLength(2);
    expect(
      screen.queryByRole("button", { name: /proposal|promot|rollback/i }),
    ).not.toBeInTheDocument();
  });

  it.each([
    [
      "both target fields",
      { revision_id: "revision-1", proposal_id: "proposal-1" },
    ],
    ["neither target field", { revision_id: null, proposal_id: null }],
  ])("fails closed when %s is malformed", async (_label, target) => {
    vi.spyOn(global, "fetch").mockResolvedValue(response(run(target)));
    renderPage(run(target));
    expect(
      await screen.findByText(
        "Evaluation target provenance could not be verified.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Persisted Skill revision"),
    ).not.toBeInTheDocument();
  });

  it.each([
    [
      "a result belongs to another run",
      [
        { ...run().case_results[0], evaluation_run_id: "other-run" },
        run().case_results[1],
      ],
    ],
    [
      "a result names an unknown case",
      [
        { ...run().case_results[0], case_id: "unknown-case" },
        run().case_results[1],
      ],
    ],
    [
      "a case has duplicate results",
      [run().case_results[0], run().case_results[0]],
    ],
  ])("fails closed when %s", async (_label, caseResults) => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      response(run({ case_results: caseResults })),
    );
    renderPage(run({ case_results: caseResults }));
    expect(
      await screen.findByText(
        "Evaluation case result provenance could not be verified.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("list", { name: "Frozen evaluation case results" }),
    ).not.toBeInTheDocument();
  });

  it("warns about a missing result without fabricating pass or fail", async () => {
    const missing = [run().case_results[0]];
    vi.spyOn(global, "fetch").mockResolvedValue(
      response(run({ case_results: missing })),
    );
    renderPage(run({ case_results: missing }));
    expect(
      await screen.findByText(/missing case result\(s\): case-a/),
    ).toBeInTheDocument();
    const results = screen.getByRole("list", {
      name: "Frozen evaluation case results",
    });
    expect(within(results).getByText("Frozen A")).toBeInTheDocument();
    expect(within(results).getAllByText("No result").length).toBeGreaterThan(0);
  });
});
