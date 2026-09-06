import type { EvaluationSuite } from "@friday/contracts";
import { useState } from "react";
import {
  useSkillEvaluationSuite,
  useSkillEvaluationSuites,
} from "../hooks/use-skill-evaluations";
import { CreateEvaluationSuiteForm } from "./create-evaluation-suite-form";
import { DeterministicEvaluationForm } from "./deterministic-evaluation-form";

function formatTime(value: string) {
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.valueOf()) ? value : timestamp.toLocaleString();
}

function formatJson(value: unknown): string {
  const formatted = JSON.stringify(value, null, 2);
  return formatted === undefined ? "Unable to format value" : formatted;
}

function orderedCases(suite: EvaluationSuite) {
  return [...suite.cases].sort((left, right) => left.position - right.position);
}

function EvaluationSuiteInspection({
  suite,
  activeRevisionId,
  onViewEvaluationRun,
}: {
  suite: EvaluationSuite;
  activeRevisionId: string | null;
  onViewEvaluationRun: (runId: string) => void;
}) {
  const cases = orderedCases(suite);
  return (
    <article aria-label={`Evaluation suite ${suite.name}`}>
      <h4>{suite.name}</h4>
      <dl>
        <dt>Suite ID</dt>
        <dd>{suite.id}</dd>
        <dt>Name</dt>
        <dd>{suite.name}</dd>
        <dt>Description</dt>
        <dd>{suite.description || "No description"}</dd>
        <dt>Status</dt>
        <dd>{suite.status}</dd>
        <dt>Created at</dt>
        <dd>{formatTime(suite.created_at)}</dd>
        <dt>Updated at</dt>
        <dd>{formatTime(suite.updated_at)}</dd>
        <dt>Case count</dt>
        <dd>{cases.length}</dd>
      </dl>

      <h5>Cases</h5>
      <ol aria-label={`Cases for evaluation suite ${suite.name}`}>
        {cases.map((item) => (
          <li key={item.id}>
            <article aria-label={`Evaluation case ${item.position}`}>
              <h6>Case {item.position}</h6>
              <dl>
                <dt>Case ID</dt>
                <dd>{item.id}</dd>
                <dt>Position</dt>
                <dd>{item.position}</dd>
                <dt>Input</dt>
                <dd>
                  <pre style={{ whiteSpace: "pre-wrap" }}>{item.input}</pre>
                </dd>
                <dt>Grading kind</dt>
                <dd>{item.grading_kind}</dd>
                <dt>Expected properties</dt>
                <dd>
                  <pre style={{ whiteSpace: "pre-wrap" }}>
                    {formatJson(item.expected_properties)}
                  </pre>
                </dd>
                <dt>Created at</dt>
                <dd>{formatTime(item.created_at)}</dd>
                <dt>Updated at</dt>
                <dd>{formatTime(item.updated_at)}</dd>
              </dl>
            </article>
          </li>
        ))}
      </ol>

      {suite.status === "active" ? (
        <DeterministicEvaluationForm
          key={suite.id}
          skillId={suite.skill_id}
          activeRevisionId={activeRevisionId}
          suiteId={suite.id}
          cases={cases}
          onRunCreated={(run) => onViewEvaluationRun(run.id)}
        />
      ) : (
        <p>
          Deterministic grading is unavailable because this persisted suite is
          disabled. The suite remains inspectable.
        </p>
      )}
    </article>
  );
}

export function SkillEvaluationSuitesSection({
  skillId,
  activeRevisionId,
  onViewEvaluationRun,
}: {
  skillId: string;
  activeRevisionId: string | null;
  onViewEvaluationRun: (runId: string) => void;
}) {
  const suites = useSkillEvaluationSuites(skillId);
  const [selectedSuiteId, setSelectedSuiteId] = useState<string | null>(null);
  const selectedSuite = useSkillEvaluationSuite(selectedSuiteId);
  const suiteItems = suites.data ?? [];
  const hasSuiteProvenanceMismatch = suiteItems.some(
    (suite) => suite.skill_id !== skillId,
  );
  const selectedSuiteData =
    selectedSuite.data &&
    selectedSuite.data.id === selectedSuiteId &&
    selectedSuite.data.skill_id === skillId
      ? selectedSuite.data
      : undefined;
  const selectedSuiteMismatch =
    selectedSuite.isSuccess &&
    (selectedSuite.data?.id !== selectedSuiteId ||
      selectedSuite.data?.skill_id !== skillId);

  return (
    <section aria-labelledby="skill-evaluation-suites-heading">
      <h3 id="skill-evaluation-suites-heading">Evaluation suites</h3>
      <p>
        Evaluation is offline evidence. It grades explicitly supplied outputs;
        it does not execute production work, grant authority, or activate or
        modify a Skill.
      </p>
      {suites.isLoading && (
        <p role="status">Loading Skill evaluation suites...</p>
      )}
      {suites.isError && (
        <p role="alert">
          Failed to load Skill evaluation suites. Skill lifecycle, revision
          history, and usage evidence remain available.
        </p>
      )}
      {!suites.isLoading && !suites.isError && hasSuiteProvenanceMismatch && (
        <p role="alert">Evaluation suite provenance could not be verified.</p>
      )}
      {!suites.isLoading &&
        !suites.isError &&
        !hasSuiteProvenanceMismatch &&
        suiteItems.length === 0 && <p>No evaluation suites yet.</p>}
      {!suites.isLoading &&
        !suites.isError &&
        !hasSuiteProvenanceMismatch &&
        suiteItems.length > 0 && (
          <ol aria-label="Skill evaluation suites">
            {suiteItems.map((suite) => (
              <li key={suite.id}>
                <article aria-label={`Evaluation suite summary ${suite.name}`}>
                  <h4>{suite.name}</h4>
                  <dl>
                    <dt>Suite ID</dt>
                    <dd>{suite.id}</dd>
                    <dt>Name</dt>
                    <dd>{suite.name}</dd>
                    <dt>Description</dt>
                    <dd>{suite.description || "No description"}</dd>
                    <dt>Status</dt>
                    <dd>{suite.status}</dd>
                    <dt>Created at</dt>
                    <dd>{formatTime(suite.created_at)}</dd>
                    <dt>Updated at</dt>
                    <dd>{formatTime(suite.updated_at)}</dd>
                    <dt>Case count</dt>
                    <dd>{suite.cases.length}</dd>
                  </dl>
                  <button
                    type="button"
                    onClick={() => setSelectedSuiteId(suite.id)}
                  >
                    Inspect suite
                  </button>
                </article>
              </li>
            ))}
          </ol>
        )}

      {selectedSuiteId !== null && selectedSuite.isLoading && (
        <p role="status">Loading exact evaluation suite...</p>
      )}
      {selectedSuiteId !== null && selectedSuite.isError && (
        <p role="alert">Failed to load exact evaluation suite.</p>
      )}
      {selectedSuiteMismatch && (
        <p role="alert">Evaluation suite provenance could not be verified.</p>
      )}
      {selectedSuiteData && (
        <section aria-labelledby="selected-evaluation-suite-heading">
          <h4 id="selected-evaluation-suite-heading">
            Selected evaluation suite
          </h4>
          <EvaluationSuiteInspection
            suite={selectedSuiteData}
            activeRevisionId={activeRevisionId}
            onViewEvaluationRun={onViewEvaluationRun}
          />
        </section>
      )}

      <CreateEvaluationSuiteForm
        skillId={skillId}
        onCreated={(suite) => setSelectedSuiteId(suite.id)}
      />
    </section>
  );
}
