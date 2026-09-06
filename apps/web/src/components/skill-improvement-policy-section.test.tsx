import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SkillImprovementPolicySection } from "./skill-improvement-policy-section";

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const suite = {
  id: "suite-1",
  skill_id: "skill-1",
  name: "Safety suite",
  description: "Bounded checks.",
  status: "disabled",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  cases: [
    {
      id: "case-1",
      suite_id: "suite-1",
      position: 1,
      input: "input",
      expected_properties: {},
      grading_kind: "exact_match",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
  ],
};

const policy = {
  skill_id: "skill-1",
  enabled: true,
  minimum_usage_records: 1,
  minimum_failures: 0,
  minimum_harmful_feedback: 0,
  evaluation_suite_id: "suite-1",
  cooldown_seconds: 3600,
  max_open_proposals: 1,
  evidence_window_size: 20,
  generator_version: "brain-candidate-generator-v2",
  comparison_policy_version: "comparison-v1",
  last_triggered_at: null,
};

function renderSection() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <SkillImprovementPolicySection skillId="skill-1" />
    </QueryClientProvider>,
  );
}

describe("SkillImprovementPolicySection", () => {
  afterEach(() => vi.restoreAllMocks());

  it("treats only policy 404 as absence and saves one exact local draft", async () => {
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        if (method === "GET" && url.endsWith("/improvement-policy")) {
          return response(
            { error: { type: "skill_not_found", message: "missing" } },
            404,
          );
        }
        if (method === "GET" && url.endsWith("/evaluation-suites"))
          return response([suite]);
        if (method === "PUT" && url.endsWith("/improvement-policy"))
          return response(policy);
        return response(
          { error: { type: "unexpected", message: "unexpected" } },
          500,
        );
      });
    renderSection();

    expect(
      await screen.findByText(
        "No improvement policy is configured for this Skill.",
      ),
    ).toBeInTheDocument();
    const user = userEvent.setup();
    await screen.findByRole("option", { name: /Safety suite/ });
    await user.selectOptions(
      screen.getByLabelText("Evaluation suite"),
      "suite-1",
    );
    await user.clear(screen.getByLabelText("Evidence window size"));
    await user.type(screen.getByLabelText("Evidence window size"), "20");
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          String(input).endsWith("/improvement-policy") &&
          (init as RequestInit | undefined)?.method === "PUT",
      ),
    ).toBe(false);

    await user.click(
      screen.getByRole("button", { name: "Save improvement policy" }),
    );
    expect(
      await screen.findByText("Improvement policy saved."),
    ).toBeInTheDocument();
    const puts = fetchMock.mock.calls.filter(
      ([input, init]) =>
        String(input).endsWith("/improvement-policy") &&
        (init as RequestInit | undefined)?.method === "PUT",
    );
    expect(puts).toHaveLength(1);
    expect(JSON.parse(String((puts[0]?.[1] as RequestInit).body))).toEqual({
      enabled: true,
      minimum_usage_records: 1,
      minimum_failures: 0,
      minimum_harmful_feedback: 0,
      evaluation_suite_id: "suite-1",
      cooldown_seconds: 3600,
      max_open_proposals: 1,
      evidence_window_size: 20,
      generator_version: "brain-candidate-generator-v2",
      comparison_policy_version: "comparison-v1",
    });
  });

  it("keeps due semantics inert and does not claim the Skill is healthy", async () => {
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        if (method === "GET" && url.endsWith("/improvement-policy"))
          return response(policy);
        if (method === "GET" && url.endsWith("/evaluation-suites"))
          return response([suite]);
        if (method === "POST" && url.endsWith("/run-now"))
          return response({ due: false });
        return response(
          { error: { type: "unexpected", message: "unexpected" } },
          500,
        );
      });
    renderSection();

    await userEvent.setup().click(
      await screen.findByRole("button", {
        name: "Request improvement processing",
      }),
    );
    expect(
      await screen.findByText(
        "No improvement processing was due under the current persisted policy.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Skill is healthy/i)).not.toBeInTheDocument();
    const posts = fetchMock.mock.calls.filter(
      ([input, init]) =>
        String(input).endsWith("/run-now") &&
        (init as RequestInit | undefined)?.method === "POST",
    );
    expect(posts).toHaveLength(1);
  });
});
