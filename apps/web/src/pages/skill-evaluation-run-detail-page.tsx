import type { EvaluationCaseResult, EvaluationRun } from "@friday/contracts";
import { useSkillEvaluationRun } from "../hooks/use-skill-evaluations";

interface SnapshotCase {
  id: string;
  position: number;
  input: string;
  expected_properties: unknown;
  grading_kind: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function formatJson(value: unknown): string {
  const formatted = JSON.stringify(value, null, 2);
  return formatted === undefined ? "Unable to format value" : formatted;
}

function targetType(
  run: EvaluationRun,
): { kind: "revision"; id: string } | { kind: "proposal"; id: string } | null {
  if (
    run.revision_id !== null &&
    run.revision_id.length > 0 &&
    run.proposal_id === null
  )
    return { kind: "revision", id: run.revision_id };
  if (
    run.revision_id === null &&
    run.proposal_id !== null &&
    run.proposal_id.length > 0
  )
    return { kind: "proposal", id: run.proposal_id };
  return null;
}

function snapshotCases(snapshot: unknown): SnapshotCase[] | null {
  if (
    !isRecord(snapshot) ||
    !Array.isArray(snapshot.cases) ||
    snapshot.cases.length === 0
  )
    return null;
  const ids = new Set<string>();
  const positions = new Set<number>();
  const cases: SnapshotCase[] = [];
  for (const item of snapshot.cases) {
    if (
      !isRecord(item) ||
      typeof item.id !== "string" ||
      item.id.length === 0 ||
      typeof item.position !== "number" ||
      !Number.isInteger(item.position) ||
      typeof item.input !== "string" ||
      !("expected_properties" in item) ||
      typeof item.grading_kind !== "string" ||
      ids.has(item.id) ||
      positions.has(item.position)
    ) {
      return null;
    }
    ids.add(item.id);
    positions.add(item.position);
    cases.push({
      id: item.id,
      position: item.position,
      input: item.input,
      expected_properties: item.expected_properties,
      grading_kind: item.grading_kind,
    });
  }
  const ordered = cases.sort((left, right) => left.position - right.position);
  return ordered.every((item, index) => item.position === index + 1)
    ? ordered
    : null;
}

function verifiedResults(
  run: EvaluationRun,
  cases: SnapshotCase[],
):
  | {
      valid: true;
      byCaseId: Map<string, EvaluationCaseResult>;
      missing: string[];
    }
  | { valid: false } {
  const caseIds = new Set(cases.map((item) => item.id));
  const byCaseId = new Map<string, EvaluationCaseResult>();
  for (const result of run.case_results) {
    if (
      result.evaluation_run_id !== run.id ||
      !caseIds.has(result.case_id) ||
      byCaseId.has(result.case_id)
    ) {
      return { valid: false };
    }
    byCaseId.set(result.case_id, result);
  }
  return {
    valid: true,
    byCaseId,
    missing: cases
      .filter((item) => !byCaseId.has(item.id))
      .map((item) => item.id),
  };
}

function EvaluationCaseResultInspection({
  item,
  result,
}: {
  item: SnapshotCase;
  result: EvaluationCaseResult | undefined;
}) {
  return (
    <li>
      <article aria-label={`Evaluation case result ${item.position}`}>
        <h5>Case {item.position}</h5>
        <dl>
          <dt>Case ID</dt>
          <dd>{item.id}</dd>
          <dt>Position</dt>
          <dd>{item.position}</dd>
          <dt>Frozen input</dt>
          <dd>
            <pre style={{ whiteSpace: "pre-wrap" }}>{item.input}</pre>
          </dd>
          <dt>Frozen grading kind</dt>
          <dd>{item.grading_kind}</dd>
          {item.grading_kind === "tool_proposal_shape" && (
            <>
              <dt>Tool proposal safety</dt>
              <dd>
                This evaluator validates proposal structure only. No proposed
                tool is executed.
              </dd>
            </>
          )}
          <dt>Frozen expected properties</dt>
          <dd>
            <pre style={{ whiteSpace: "pre-wrap" }}>
              {formatJson(item.expected_properties)}
            </pre>
          </dd>
          <dt>Result status</dt>
          <dd>{result?.status ?? "No result"}</dd>
          <dt>Score</dt>
          <dd>{result?.score ?? "No result"}</dd>
          <dt>Reason code</dt>
          <dd>{result?.reason_code ?? "None"}</dd>
          <dt>Bounded details</dt>
          <dd>{result?.bounded_details ?? "No result available"}</dd>
          <dt>Output SHA-256</dt>
          <dd>{result?.output_sha256 ?? "No result"}</dd>
        </dl>
      </article>
    </li>
  );
}

export function SkillEvaluationRunDetailPage({
  runId,
  onBackToSkill,
}: {
  runId: string;
  onBackToSkill: (skillId: string) => void;
}) {
  const runQuery = useSkillEvaluationRun(runId);

  if (runQuery.isLoading) return <p>Loading Evaluation Run...</p>;
  if (runQuery.isError || !runQuery.data)
    return <p role="alert">Failed to load Evaluation Run.</p>;

  const run = runQuery.data;
  if (run.id !== runId)
    return <p role="alert">Evaluation Run provenance could not be verified.</p>;
  const target = targetType(run);
  const cases = snapshotCases(run.suite_snapshot);
  const resultState = cases === null ? null : verifiedResults(run, cases);
  const missingResults =
    resultState !== null && resultState.valid ? resultState.missing : [];

  return (
    <section>
      <button type="button" onClick={() => onBackToSkill(run.skill_id)}>
        Back to Skill
      </button>
      <h2>Evaluation Run Detail</h2>
      <dl>
        <dt>Evaluation Run ID</dt>
        <dd>{run.id}</dd>
        <dt>Suite ID</dt>
        <dd>{run.suite_id}</dd>
        <dt>Skill ID</dt>
        <dd>{run.skill_id}</dd>
        <dt>Status</dt>
        <dd>{run.status}</dd>
        <dt>Target content SHA-256</dt>
        <dd>{run.target_content_sha256}</dd>
        <dt>Runtime fingerprint</dt>
        <dd>{run.runtime_fingerprint}</dd>
        <dt>Proposal ID</dt>
        <dd>{run.proposal_id ?? "None"}</dd>
      </dl>
      <p>
        The runtime fingerprint identifies the code-owned evaluation
        configuration used for this immutable Evaluation Run.
      </p>

      {target === null ? (
        <p role="alert">Evaluation target provenance could not be verified.</p>
      ) : (
        <dl>
          <dt>Target type</dt>
          <dd>
            {target.kind === "revision"
              ? "Persisted Skill revision"
              : "Improvement proposal candidate (inspection only)"}
          </dd>
          <dt>{target.kind === "revision" ? "Revision ID" : "Proposal ID"}</dt>
          <dd>{target.id}</dd>
        </dl>
      )}

      <h3>Aggregate result</h3>
      <pre style={{ whiteSpace: "pre-wrap" }}>
        {formatJson(run.aggregate_result)}
      </pre>

      <h3>Runtime metadata</h3>
      <pre style={{ whiteSpace: "pre-wrap" }}>
        {formatJson(run.runtime_metadata)}
      </pre>

      <h3>Frozen suite snapshot</h3>
      <pre style={{ whiteSpace: "pre-wrap" }}>
        {formatJson(run.suite_snapshot)}
      </pre>

      <h3>Case results</h3>
      {cases === null || resultState === null || !resultState.valid ? (
        <p role="alert">
          Evaluation case result provenance could not be verified.
        </p>
      ) : (
        <>
          {missingResults.length > 0 && (
            <p role="alert">
              Evaluation case result provenance could not be verified for
              missing case result(s): {missingResults.join(", ")}.
            </p>
          )}
          <ol aria-label="Frozen evaluation case results">
            {cases.map((item) => (
              <EvaluationCaseResultInspection
                key={item.id}
                item={item}
                result={resultState.byCaseId.get(item.id)}
              />
            ))}
          </ol>
        </>
      )}
    </section>
  );
}
