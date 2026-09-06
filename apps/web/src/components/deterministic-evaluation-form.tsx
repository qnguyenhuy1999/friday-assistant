import type {
  EvaluationCase,
  EvaluationRun,
  SkillRevision,
} from "@friday/contracts";
import { useState } from "react";
import { useSkillRevision, useSkillRevisions } from "../hooks/use-skills";
import { useRunSkillEvaluation } from "../hooks/use-skill-evaluations";

function uniqueRevisions(pages: SkillRevision[][]): SkillRevision[] {
  const seen = new Set<string>();
  return pages.flat().filter((revision) => {
    if (seen.has(revision.id)) return false;
    seen.add(revision.id);
    return true;
  });
}

function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "The server rejected this evaluation.";
}

function orderedCases(cases: EvaluationCase[]): EvaluationCase[] {
  return [...cases].sort((left, right) => left.position - right.position);
}

export function DeterministicEvaluationForm({
  skillId,
  activeRevisionId,
  suiteId,
  cases,
  onRunCreated,
}: {
  skillId: string;
  activeRevisionId: string | null;
  suiteId: string;
  cases: EvaluationCase[];
  onRunCreated: (run: EvaluationRun) => void;
}) {
  const revisions = useSkillRevisions(skillId);
  const activeRevision = useSkillRevision(skillId, activeRevisionId);
  const run = useRunSkillEvaluation(suiteId);
  const [selectedRevisionId, setSelectedRevisionId] = useState("");
  const [outputs, setOutputs] = useState<Record<string, string>>(() =>
    Object.fromEntries(cases.map((item) => [item.id, ""])),
  );
  const [validationError, setValidationError] = useState<string | null>(null);

  const loadedRevisions = uniqueRevisions(revisions.data?.pages ?? []);
  const revisionCandidates = [...loadedRevisions];
  if (
    activeRevision.data &&
    !revisionCandidates.some(
      (revision) => revision.id === activeRevision.data.id,
    )
  ) {
    revisionCandidates.push(activeRevision.data);
  }
  revisionCandidates.sort((left, right) => right.version - left.version);
  const hasRevisionProvenanceMismatch =
    revisionCandidates.some((revision) => revision.skill_id !== skillId) ||
    (activeRevision.isSuccess && activeRevision.data.skill_id !== skillId);
  const selectableRevisions = revisionCandidates.filter(
    (revision) => revision.skill_id === skillId,
  );
  const selectedRevision = selectableRevisions.find(
    (revision) => revision.id === selectedRevisionId,
  );
  const displayCases = orderedCases(cases);

  function submit() {
    if (hasRevisionProvenanceMismatch) {
      setValidationError(
        "Evaluation revision provenance could not be verified.",
      );
      return;
    }
    if (!selectedRevision) {
      setValidationError("Select an exact persisted Skill revision first.");
      return;
    }
    const exactOutputs = Object.fromEntries(
      displayCases.map((item) => [item.id, outputs[item.id] ?? ""]),
    );
    setValidationError(null);
    run.mutate(
      { revision_id: selectedRevision.id, outputs: exactOutputs },
      { onSuccess: onRunCreated },
    );
  }

  return (
    <section aria-labelledby="deterministic-evaluation-heading">
      <h4 id="deterministic-evaluation-heading">Deterministic grading</h4>
      <p>
        This deterministic evaluation grades supplied outputs against the frozen
        evaluation suite. It does not run the Skill, call a model, invoke tools,
        or perform external side effects.
      </p>

      {revisions.isLoading && (
        <p role="status">Loading bounded Skill revision history...</p>
      )}
      {revisions.isError && (
        <p role="alert">Failed to load Skill revisions for evaluation.</p>
      )}
      {hasRevisionProvenanceMismatch && (
        <p role="alert">
          Evaluation revision provenance could not be verified.
        </p>
      )}

      <label htmlFor="evaluation-target-revision">Target revision</label>
      <select
        id="evaluation-target-revision"
        value={selectedRevisionId}
        onChange={(event) => {
          setSelectedRevisionId(event.target.value);
          setValidationError(null);
        }}
        disabled={hasRevisionProvenanceMismatch}
      >
        <option value="">Select an exact persisted revision</option>
        {selectableRevisions.map((revision) => (
          <option key={revision.id} value={revision.id}>
            v{revision.version}
            {revision.id === activeRevisionId ? " - active" : ""}
          </option>
        ))}
      </select>
      {activeRevision.isLoading && activeRevisionId !== null && (
        <p role="status">Verifying the active revision metadata...</p>
      )}
      {revisions.hasNextPage && (
        <button
          type="button"
          disabled={revisions.isFetchingNextPage}
          onClick={() => void revisions.fetchNextPage()}
        >
          {revisions.isFetchingNextPage
            ? "Loading older revisions..."
            : "Load older revisions"}
        </button>
      )}

      {selectedRevision && (
        <dl>
          <dt>Exact revision ID</dt>
          <dd>{selectedRevision.id}</dd>
          <dt>Version</dt>
          <dd>v{selectedRevision.version}</dd>
          <dt>Content SHA-256</dt>
          <dd>{selectedRevision.content_sha256}</dd>
          <dt>Source</dt>
          <dd>{selectedRevision.source_kind}</dd>
        </dl>
      )}

      <ol aria-label="Supplied evaluation outputs">
        {displayCases.map((item, index) => (
          <li key={item.id}>
            <fieldset aria-label={`Evaluation case ${item.position}`}>
              <legend>
                Case {item.position} — {item.id}
              </legend>
              <dl>
                <dt>Frozen input</dt>
                <dd>
                  <pre style={{ whiteSpace: "pre-wrap" }}>{item.input}</pre>
                </dd>
                <dt>Frozen grading kind</dt>
                <dd>{item.grading_kind}</dd>
              </dl>
              {item.grading_kind === "tool_proposal_shape" && (
                <p>
                  This evaluator validates proposal structure only. No proposed
                  tool is executed.
                </p>
              )}
              <label htmlFor={`supplied-output-${item.id}`}>
                Supplied output
              </label>
              <textarea
                id={`supplied-output-${item.id}`}
                value={outputs[item.id] ?? ""}
                onChange={(event) =>
                  setOutputs((current) => ({
                    ...current,
                    [item.id]: event.target.value,
                  }))
                }
              />
            </fieldset>
            {index < displayCases.length - 1 && <hr />}
          </li>
        ))}
      </ol>

      <button
        type="button"
        onClick={submit}
        disabled={run.isPending || hasRevisionProvenanceMismatch}
      >
        Grade supplied outputs
      </button>
      {validationError && <p role="alert">{validationError}</p>}
      {run.isError && (
        <p role="alert">
          Failed to grade supplied outputs: {errorMessage(run.error)}
        </p>
      )}
    </section>
  );
}
