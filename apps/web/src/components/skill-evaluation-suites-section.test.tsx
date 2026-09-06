import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CreateEvaluationSuiteForm } from "./create-evaluation-suite-form";
import { SkillEvaluationSuitesSection } from "./skill-evaluation-suites-section";

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function evaluationCase(
  id: string,
  position: number,
  input: string,
  gradingKind: string,
  expectedProperties: Record<string, unknown>,
) {
  return {
    id,
    suite_id: "suite-1",
    position,
    input,
    expected_properties: expectedProperties,
    grading_kind: gradingKind,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

const suite = {
  id: "suite-1",
  skill_id: "skill-1",
  name: "Safety suite",
  description: "Offline checks.",
  status: "active",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
  cases: [
    evaluationCase("case-a", 1, "Return a safe answer", "exact_match", {
      value: "SAFE",
    }),
    evaluationCase("case-b", 2, "Avoid dangerous language", "contains_none", {
      values: ["DANGEROUS"],
    }),
  ],
};

const revisions = [
  {
    id: "revision-v2",
    skill_id: "skill-1",
    version: 2,
    instructions: "Second instructions",
    content_sha256: "b".repeat(64),
    source_kind: "operator",
    created_at: "2026-01-02T00:00:00Z",
  },
  {
    id: "revision-v1",
    skill_id: "skill-1",
    version: 1,
    instructions: "First instructions",
    content_sha256: "a".repeat(64),
    source_kind: "operator",
    created_at: "2026-01-01T00:00:00Z",
  },
];

function renderForm(onCreated = vi.fn()) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <CreateEvaluationSuiteForm skillId="skill-1" onCreated={onCreated} />
    </QueryClientProvider>,
  );
  return { onCreated };
}

function renderSection(
  onViewEvaluationRun = vi.fn(),
  activeRevisionId: string | null = "revision-v2",
) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <SkillEvaluationSuitesSection
        skillId="skill-1"
        activeRevisionId={activeRevisionId}
        onViewEvaluationRun={onViewEvaluationRun}
      />
    </QueryClientProvider>,
  );
  return { onViewEvaluationRun };
}

describe("CreateEvaluationSuiteForm", () => {
  afterEach(() => vi.restoreAllMocks());

  it("keeps case edits, ordering, and removal local until one explicit create", async () => {
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValue(response(suite, 201));
    renderForm();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Suite name"), "Local draft");
    const first = screen.getByRole("group", { name: "Evaluation case 1" });
    await user.type(within(first).getByLabelText("Case input"), "A");
    await user.click(screen.getByRole("button", { name: "Add case" }));
    const second = screen.getByRole("group", { name: "Evaluation case 2" });
    await user.type(within(second).getByLabelText("Case input"), "B");

    expect(fetchMock).not.toHaveBeenCalled();
    await user.click(
      within(second).getByRole("button", { name: "Move case 2 up" }),
    );
    expect(
      within(
        screen.getByRole("group", { name: "Evaluation case 1" }),
      ).getByLabelText("Case input"),
    ).toHaveValue("B");
    await user.click(
      within(
        screen.getByRole("group", { name: "Evaluation case 2" }),
      ).getByRole("button", { name: "Remove case" }),
    );
    expect(
      screen.getAllByRole("group", { name: /Evaluation case/ }),
    ).toHaveLength(1);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("serializes every supported evaluator shape without arbitrary keys", async () => {
    const canonical = {
      ...suite,
      cases: Array.from({ length: 6 }, (_, index) =>
        evaluationCase(
          `case-${index + 1}`,
          index + 1,
          `Input ${index + 1}`,
          "exact_match",
          {
            value: "expected",
          },
        ),
      ),
    };
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValue(response(canonical, 201));
    renderForm();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Suite name"), "All evaluators");
    const kinds = [
      "exact_match",
      "contains_all",
      "contains_none",
      "required_keys",
      "json_schema",
      "tool_proposal_shape",
    ];
    for (let index = 0; index < kinds.length; index += 1) {
      const group = screen.getByRole("group", {
        name: `Evaluation case ${index + 1}`,
      });
      await user.type(
        within(group).getByLabelText("Case input"),
        `Input ${index + 1}`,
      );
      await user.selectOptions(
        within(group).getByLabelText("Grading kind"),
        kinds[index]!,
      );
      if (index === 0)
        await user.type(
          within(group).getByLabelText("Expected exact value"),
          "expected",
        );
      if (index === 1) {
        await user.type(within(group).getByLabelText("Expected value 1"), "a");
        await user.click(
          within(group).getByRole("button", { name: "Add expected value" }),
        );
        await user.type(within(group).getByLabelText("Expected value 2"), "b");
      }
      if (index === 2)
        await user.type(within(group).getByLabelText("Forbidden value 1"), "x");
      if (index === 3)
        await user.type(
          within(group).getByLabelText("Required key 1"),
          "summary",
        );
      if (index === 4)
        await user.clear(
          within(group).getByLabelText("JSON Schema expectation"),
        );
      if (index === 4)
        fireEvent.change(
          within(group).getByLabelText("JSON Schema expectation"),
          { target: { value: '{"type":"object"}' } },
        );
      if (index === 5) {
        await user.type(
          within(group).getByLabelText("Required tool (optional)"),
          "shell.run",
        );
        await user.click(
          within(group).getByRole("button", { name: "Add required input key" }),
        );
        await user.type(
          within(group).getByLabelText("Required input key 1 (optional)"),
          "command",
        );
      }
      if (index < kinds.length - 1)
        await user.click(screen.getByRole("button", { name: "Add case" }));
    }
    await user.click(
      screen.getByRole("button", { name: "Create evaluation suite" }),
    );

    const post = fetchMock.mock.calls.find(
      ([input, init]) =>
        new URL(String(input)).pathname ===
          "/v1/skills/skill-1/evaluation-suites" &&
        (init as RequestInit | undefined)?.method === "POST",
    );
    expect(post).toBeDefined();
    expect(JSON.parse(String((post?.[1] as RequestInit).body))).toMatchObject({
      name: "All evaluators",
      cases: [
        {
          expected_properties: { value: "expected" },
          grading_kind: "exact_match",
        },
        {
          expected_properties: { values: ["a", "b"] },
          grading_kind: "contains_all",
        },
        {
          expected_properties: { values: ["x"] },
          grading_kind: "contains_none",
        },
        {
          expected_properties: { required_keys: ["summary"] },
          grading_kind: "required_keys",
        },
        {
          expected_properties: { schema: { type: "object" } },
          grading_kind: "json_schema",
        },
        {
          expected_properties: {
            required_tool: "shell.run",
            required_input_keys: ["command"],
          },
          grading_kind: "tool_proposal_shape",
        },
      ],
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("rejects invalid JSON locally and retains the draft without posting", async () => {
    const fetchMock = vi.spyOn(global, "fetch");
    renderForm();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Suite name"), "Invalid schema");
    const group = screen.getByRole("group", { name: "Evaluation case 1" });
    await user.type(within(group).getByLabelText("Case input"), "Input");
    await user.selectOptions(
      within(group).getByLabelText("Grading kind"),
      "json_schema",
    );
    await user.clear(within(group).getByLabelText("JSON Schema expectation"));
    fireEvent.change(within(group).getByLabelText("JSON Schema expectation"), {
      target: { value: "{" },
    });
    await user.click(
      screen.getByRole("button", { name: "Create evaluation suite" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Case 1 JSON schema must be valid JSON.",
    );
    expect(fetchMock).not.toHaveBeenCalled();
    expect(within(group).getByLabelText("Case input")).toHaveValue("Input");
  });

  it("keeps the draft after a server rejection and does not retry", async () => {
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValue(
        response(
          { error: { type: "entity_conflict", message: "suite rejected" } },
          409,
        ),
      );
    renderForm();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Suite name"), "Rejected suite");
    const group = screen.getByRole("group", { name: "Evaluation case 1" });
    await user.type(within(group).getByLabelText("Case input"), "Input");
    await user.click(
      screen.getByRole("button", { name: "Create evaluation suite" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "suite rejected",
    );
    expect(screen.getByLabelText("Suite name")).toHaveValue("Rejected suite");
    expect(within(group).getByLabelText("Case input")).toHaveValue("Input");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("SkillEvaluationSuitesSection", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders an independent empty state", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(response([]));
    renderSection();
    expect(
      await screen.findByText("No evaluation suites yet."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Create evaluation suite" }),
    ).toBeInTheDocument();
  });

  it("loads exact suite detail and grades outputs against the explicitly selected old revision", async () => {
    let created = false;
    const evaluationRun = {
      id: "eval-run-123",
      suite_id: "suite-1",
      skill_id: "skill-1",
      revision_id: "revision-v1",
      proposal_id: null,
      status: "succeeded",
      aggregate_result: { case_count: 2, passed: 2, score: 1 },
      runtime_fingerprint: "d".repeat(64),
      target_content_sha256: "a".repeat(64),
      runtime_metadata: { evaluator_version: "deterministic-v1" },
      suite_snapshot: {
        cases: suite.cases.map(
          ({ id, position, input, expected_properties, grading_kind }) => ({
            id,
            position,
            input,
            expected_properties,
            grading_kind,
          }),
        ),
      },
      case_results: suite.cases.map((item) => ({
        evaluation_run_id: "eval-run-123",
        case_id: item.id,
        status: "succeeded",
        score: 1,
        reason_code: null,
        bounded_details: "passed",
        output_sha256: "c".repeat(64),
      })),
    };
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockImplementation(async (input, init) => {
        const url = new URL(String(input));
        const method = init?.method ?? "GET";
        if (
          method === "GET" &&
          url.pathname === "/v1/skills/skill-1/evaluation-suites"
        )
          return response([suite]);
        if (
          method === "GET" &&
          url.pathname === "/v1/skills/evaluation-suites/suite-1"
        )
          return response(suite);
        if (method === "GET" && url.pathname === "/v1/skills/skill-1/revisions")
          return response(revisions);
        if (
          method === "GET" &&
          url.pathname === "/v1/skills/skill-1/revisions/revision-v2"
        )
          return response(revisions[0]);
        if (
          method === "POST" &&
          url.pathname === "/v1/skills/evaluation-suites/suite-1/runs"
        ) {
          created = true;
          return response(evaluationRun, 201);
        }
        return response(created ? [suite] : []);
      });
    const { onViewEvaluationRun } = renderSection();
    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "Inspect suite" }));
    expect(
      await screen.findByRole("heading", { name: "Selected evaluation suite" }),
    ).toBeInTheDocument();
    const target = await screen.findByLabelText("Target revision");
    await userEvent.setup().selectOptions(target, "revision-v1");
    expect(screen.getByText("a".repeat(64))).toBeInTheDocument();
    const outputs = screen.getAllByLabelText("Supplied output");
    const user = userEvent.setup();
    await user.type(outputs[0]!, "SAFE");
    await user.type(outputs[1]!, "normal response");
    await user.click(
      screen.getByRole("button", { name: "Grade supplied outputs" }),
    );
    expect(onViewEvaluationRun).toHaveBeenCalledWith("eval-run-123");
    const post = fetchMock.mock.calls.find(
      ([input, init]) =>
        new URL(String(input)).pathname ===
          "/v1/skills/evaluation-suites/suite-1/runs" &&
        (init as RequestInit | undefined)?.method === "POST",
    );
    expect(JSON.parse(String((post?.[1] as RequestInit).body))).toEqual({
      revision_id: "revision-v1",
      outputs: { "case-a": "SAFE", "case-b": "normal response" },
    });
  });

  it("fails closed for a suite returned for another Skill", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      response([{ ...suite, skill_id: "skill-2" }]),
    );
    renderSection();
    expect(
      await screen.findByText(
        "Evaluation suite provenance could not be verified.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Inspect suite" }),
    ).not.toBeInTheDocument();
  });
});
