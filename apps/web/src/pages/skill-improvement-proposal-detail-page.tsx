import type { EvaluationRun } from "@friday/contracts";
import {
  isNotFoundError,
  useCancelSkillImprovementProposal,
  useSkillEvidenceSnapshot,
  useSkillImprovementProposal,
  useSkillProposalEvaluation,
} from "../hooks/use-skill-improvement";
import { useSkillEvaluationRun } from "../hooks/use-skill-evaluations";
import { useSkillRevision } from "../hooks/use-skills";
import {
  errorMessage,
  formatJson,
  formatTime,
  isKnownClosedProposalStatus,
  parseEvidenceSnapshot,
} from "./skill-improvement-utils";

function runHasBaselineProvenance(
  run: EvaluationRun,
  proposalSkillId: string,
  baseRevisionId: string,
  baseContentSha: string,
) {
  return (
    run.id.length > 0 &&
    run.skill_id === proposalSkillId &&
    run.revision_id === baseRevisionId &&
    run.proposal_id === null &&
    run.target_content_sha256 === baseContentSha
  );
}

function runHasCandidateProvenance(
  run: EvaluationRun,
  proposalId: string,
  proposalSkillId: string,
  candidateContentSha: string,
) {
  return (
    run.id.length > 0 &&
    run.skill_id === proposalSkillId &&
    run.proposal_id === proposalId &&
    run.revision_id === null &&
    run.target_content_sha256 === candidateContentSha
  );
}

function EvaluationRunLinks({
  baseline,
  candidate,
  onViewEvaluationRun,
}: {
  baseline: EvaluationRun;
  candidate: EvaluationRun;
  onViewEvaluationRun: (runId: string) => void;
}) {
  return (
    <p>
      <button type="button" onClick={() => onViewEvaluationRun(baseline.id)}>
        View baseline Evaluation Run
      </button>{" "}
      <button type="button" onClick={() => onViewEvaluationRun(candidate.id)}>
        View candidate Evaluation Run
      </button>
    </p>
  );
}

export function SkillImprovementProposalDetailPage({
  proposalId,
  onBack,
  onBackToSkill,
  onViewEvaluationRun,
}: {
  proposalId: string;
  onBack: () => void;
  onBackToSkill: (skillId: string) => void;
  onViewEvaluationRun: (runId: string) => void;
}) {
  const proposal = useSkillImprovementProposal(proposalId);
  const verifiedProposal =
    proposal.data && proposal.data.id === proposalId
      ? proposal.data
      : undefined;
  const proposalProvenanceMismatch =
    proposal.isSuccess && verifiedProposal === undefined;
  const baseRevision = useSkillRevision(
    verifiedProposal?.skill_id ?? "none",
    verifiedProposal?.base_revision_id ?? null,
  );
  const evidenceSnapshot = useSkillEvidenceSnapshot(
    verifiedProposal?.evidence_snapshot_id ?? null,
  );
  const comparison = useSkillProposalEvaluation(verifiedProposal?.id ?? null);
  const comparisonWithProposal =
    comparison.data &&
    verifiedProposal &&
    comparison.data.proposal_id === verifiedProposal.id
      ? comparison.data
      : undefined;
  const comparisonProvenanceMismatch =
    comparison.isSuccess && comparisonWithProposal === undefined;
  const baselineRun = useSkillEvaluationRun(
    comparisonWithProposal?.baseline_evaluation_run_id ?? null,
  );
  const candidateRun = useSkillEvaluationRun(
    comparisonWithProposal?.candidate_evaluation_run_id ?? null,
  );
  const cancel = useCancelSkillImprovementProposal(
    verifiedProposal?.id ?? proposalId,
    verifiedProposal?.skill_id ?? "none",
  );

  if (proposal.isLoading) {
    return <p role="status">Loading exact improvement proposal...</p>;
  }
  if (proposal.isError) {
    return (
      <p role="alert">
        Failed to load improvement proposal: {errorMessage(proposal.error)}
      </p>
    );
  }
  if (proposalProvenanceMismatch || !verifiedProposal) {
    return (
      <section>
        <button type="button" onClick={onBack}>
          Back to Skills
        </button>
        <p role="alert">
          Improvement proposal provenance could not be verified.
        </p>
      </section>
    );
  }

  const baseRevisionVerified =
    baseRevision.data !== undefined &&
    baseRevision.data.id === verifiedProposal.base_revision_id &&
    baseRevision.data.skill_id === verifiedProposal.skill_id;
  const baseRevisionMismatch = baseRevision.isSuccess && !baseRevisionVerified;
  const evidenceVerified =
    evidenceSnapshot.data !== undefined &&
    evidenceSnapshot.data.id === verifiedProposal.evidence_snapshot_id &&
    evidenceSnapshot.data.skill_id === verifiedProposal.skill_id &&
    evidenceSnapshot.data.base_revision_id ===
      verifiedProposal.base_revision_id &&
    evidenceSnapshot.data.content_sha256 ===
      verifiedProposal.evidence_snapshot_hash;
  const parsedEvidence = evidenceVerified
    ? parseEvidenceSnapshot(
        evidenceSnapshot.data!,
        verifiedProposal.skill_id,
        verifiedProposal.base_revision_id,
      )
    : null;
  const comparisonAbsence =
    comparison.isError && isNotFoundError(comparison.error);
  const comparisonError = comparison.isError && !comparisonAbsence;
  const runsLoading =
    comparisonWithProposal !== undefined &&
    (baselineRun.isLoading || candidateRun.isLoading);
  const baselineRunVerified =
    comparisonWithProposal !== undefined &&
    baselineRun.data !== undefined &&
    baselineRun.data.id === comparisonWithProposal.baseline_evaluation_run_id &&
    runHasBaselineProvenance(
      baselineRun.data,
      verifiedProposal.skill_id,
      verifiedProposal.base_revision_id,
      baseRevision.data?.content_sha256 ?? "",
    );
  const candidateRunVerified =
    comparisonWithProposal !== undefined &&
    candidateRun.data !== undefined &&
    candidateRun.data.id ===
      comparisonWithProposal.candidate_evaluation_run_id &&
    runHasCandidateProvenance(
      candidateRun.data,
      verifiedProposal.id,
      verifiedProposal.skill_id,
      verifiedProposal.proposed_content_sha256,
    );
  const fairRuns =
    baselineRun.data !== undefined &&
    candidateRun.data !== undefined &&
    baselineRun.data.suite_id === candidateRun.data.suite_id &&
    baselineRun.data.runtime_fingerprint ===
      candidateRun.data.runtime_fingerprint;
  const comparisonRunsVerified =
    !runsLoading &&
    !baselineRun.isError &&
    !candidateRun.isError &&
    baseRevisionVerified &&
    baselineRunVerified &&
    candidateRunVerified &&
    fairRuns;

  function cancelProposal() {
    if (
      isKnownClosedProposalStatus(verifiedProposal?.status ?? "") ||
      !window.confirm(
        "Cancel this improvement proposal? The server remains authoritative for its lifecycle.",
      )
    ) {
      return;
    }
    cancel.mutate();
  }

  return (
    <section>
      <button type="button" onClick={onBack}>
        Back to Skills
      </button>
      <button
        type="button"
        onClick={() => onBackToSkill(verifiedProposal.skill_id)}
      >
        Back to Skill
      </button>
      <h2>Improvement proposal review</h2>
      <dl>
        <dt>Proposal ID</dt>
        <dd>{verifiedProposal.id}</dd>
        <dt>Skill ID</dt>
        <dd>{verifiedProposal.skill_id}</dd>
        <dt>Base revision ID</dt>
        <dd>{verifiedProposal.base_revision_id}</dd>
        <dt>Status</dt>
        <dd>{verifiedProposal.status}</dd>
        <dt>Evidence snapshot ID</dt>
        <dd>{verifiedProposal.evidence_snapshot_id}</dd>
        <dt>Evidence snapshot hash</dt>
        <dd>{verifiedProposal.evidence_snapshot_hash}</dd>
        <dt>Proposed content SHA-256</dt>
        <dd>{verifiedProposal.proposed_content_sha256}</dd>
        <dt>Rationale</dt>
        <dd>
          <pre style={{ whiteSpace: "pre-wrap" }}>
            {verifiedProposal.rationale}
          </pre>
        </dd>
        <dt>Generator version</dt>
        <dd>{verifiedProposal.generator_version}</dd>
        <dt>Created at</dt>
        <dd>{formatTime(verifiedProposal.created_at)}</dd>
      </dl>
      <p>
        This candidate is an inert proposal. It is not a Skill revision and is
        not used by production Runs.
      </p>

      <section aria-labelledby="base-revision-heading">
        <h3 id="base-revision-heading">Base revision</h3>
        {baseRevision.isLoading && (
          <p role="status">Loading exact base revision...</p>
        )}
        {baseRevision.isError && (
          <p role="alert">Failed to load exact base revision.</p>
        )}
        {baseRevisionMismatch && (
          <p role="alert">Base revision provenance could not be verified.</p>
        )}
        {baseRevisionVerified && (
          <dl>
            <dt>Version</dt>
            <dd>v{baseRevision.data.version}</dd>
            <dt>Revision ID</dt>
            <dd>{baseRevision.data.id}</dd>
            <dt>SHA-256</dt>
            <dd>{baseRevision.data.content_sha256}</dd>
            <dt>Source</dt>
            <dd>{baseRevision.data.source_kind}</dd>
            <dt>Exact persisted instructions</dt>
            <dd>
              <pre style={{ whiteSpace: "pre-wrap" }}>
                {baseRevision.data.instructions}
              </pre>
            </dd>
          </dl>
        )}
      </section>

      <section aria-labelledby="candidate-proposal-heading">
        <h3 id="candidate-proposal-heading">Candidate proposal</h3>
        <dl>
          <dt>Proposal ID</dt>
          <dd>{verifiedProposal.id}</dd>
          <dt>Candidate SHA-256</dt>
          <dd>{verifiedProposal.proposed_content_sha256}</dd>
          <dt>Proposed instructions</dt>
          <dd>
            <pre style={{ whiteSpace: "pre-wrap" }}>
              {verifiedProposal.proposed_instructions}
            </pre>
          </dd>
          <dt>Rationale</dt>
          <dd>
            <pre style={{ whiteSpace: "pre-wrap" }}>
              {verifiedProposal.rationale}
            </pre>
          </dd>
          <dt>Generator version</dt>
          <dd>{verifiedProposal.generator_version}</dd>
        </dl>
      </section>

      <section aria-labelledby="evidence-snapshot-heading">
        <h3 id="evidence-snapshot-heading">Frozen evidence snapshot</h3>
        <p>
          The evidence snapshot records observations selected by Friday's
          policy. It does not prove that the Skill caused any Run outcome.
        </p>
        {evidenceSnapshot.isLoading && (
          <p role="status">Loading exact evidence snapshot...</p>
        )}
        {evidenceSnapshot.isError && (
          <p role="alert">Failed to load evidence snapshot.</p>
        )}
        {evidenceSnapshot.isSuccess && !evidenceVerified && (
          <p role="alert">
            Evidence snapshot provenance could not be verified.
          </p>
        )}
        {evidenceVerified && parsedEvidence && "error" in parsedEvidence && (
          <p role="alert">{parsedEvidence.error}</p>
        )}
        {evidenceVerified && parsedEvidence && "entries" in parsedEvidence && (
          <ol aria-label="Frozen evidence entries">
            {parsedEvidence.entries.map((entry) => (
              <li key={entry.id}>
                <article aria-label={`Evidence ${entry.id}`}>
                  <h4>{entry.kind}</h4>
                  <dl>
                    <dt>Evidence ID</dt>
                    <dd>{entry.id}</dd>
                    <dt>Kind</dt>
                    <dd>{entry.kind}</dd>
                    <dt>Payload</dt>
                    <dd>
                      <pre style={{ whiteSpace: "pre-wrap" }}>
                        {formatJson(entry.payload)}
                      </pre>
                    </dd>
                  </dl>
                </article>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section aria-labelledby="candidate-comparison-heading">
        <h3 id="candidate-comparison-heading">Candidate comparison</h3>
        <p>
          Comparison result is deterministic evaluation evidence. Recommendation
          is Friday-owned evaluation output. Recommendation is not approval and
          does not activate the candidate.
        </p>
        {comparison.isLoading && (
          <p role="status">Loading candidate comparison...</p>
        )}
        {comparisonAbsence && <p>No candidate comparison is available yet.</p>}
        {comparisonError && (
          <p role="alert">
            Failed to load candidate comparison:{" "}
            {errorMessage(comparison.error)}
          </p>
        )}
        {comparisonProvenanceMismatch && (
          <p role="alert">
            Candidate comparison provenance could not be verified.
          </p>
        )}
        {comparisonWithProposal && !comparisonProvenanceMismatch && (
          <>
            {runsLoading && (
              <p role="status">
                Loading exact baseline and candidate Evaluation Runs...
              </p>
            )}
            {!runsLoading && (baselineRun.isError || candidateRun.isError) && (
              <p role="alert">
                Failed to load exact Evaluation Runs. Candidate comparison
                provenance could not be verified.
              </p>
            )}
            {!runsLoading &&
              !baselineRun.isError &&
              !candidateRun.isError &&
              !comparisonRunsVerified && (
                <p role="alert">
                  Candidate comparison Evaluation Run provenance could not be
                  verified.
                </p>
              )}
            {comparisonRunsVerified && (
              <>
                <dl>
                  <dt>Comparison ID</dt>
                  <dd>{comparisonWithProposal.id}</dd>
                  <dt>Proposal ID</dt>
                  <dd>{comparisonWithProposal.proposal_id}</dd>
                  <dt>Baseline Evaluation Run ID</dt>
                  <dd>{comparisonWithProposal.baseline_evaluation_run_id}</dd>
                  <dt>Candidate Evaluation Run ID</dt>
                  <dd>{comparisonWithProposal.candidate_evaluation_run_id}</dd>
                  <dt>Comparison policy version</dt>
                  <dd>{comparisonWithProposal.comparison_policy_version}</dd>
                  <dt>Result</dt>
                  <dd>{comparisonWithProposal.result}</dd>
                  <dt>Recommendation</dt>
                  <dd>{comparisonWithProposal.recommendation}</dd>
                  <dt>Score delta</dt>
                  <dd>{comparisonWithProposal.score_delta}</dd>
                  <dt>Regression count</dt>
                  <dd>{comparisonWithProposal.regression_count}</dd>
                  <dt>Improvement count</dt>
                  <dd>{comparisonWithProposal.improvement_count}</dd>
                  <dt>Inconclusive count</dt>
                  <dd>{comparisonWithProposal.inconclusive_count}</dd>
                  <dt>Report SHA-256</dt>
                  <dd>{comparisonWithProposal.report_sha256}</dd>
                  <dt>Comparison report</dt>
                  <dd>
                    <pre style={{ whiteSpace: "pre-wrap" }}>
                      {formatJson(comparisonWithProposal.comparison_report)}
                    </pre>
                  </dd>
                </dl>
                <EvaluationRunLinks
                  baseline={baselineRun.data!}
                  candidate={candidateRun.data!}
                  onViewEvaluationRun={onViewEvaluationRun}
                />
              </>
            )}
          </>
        )}
      </section>

      {!isKnownClosedProposalStatus(verifiedProposal.status) && (
        <section aria-labelledby="proposal-cancellation-heading">
          <h3 id="proposal-cancellation-heading">Proposal lifecycle</h3>
          <button
            type="button"
            disabled={cancel.isPending}
            onClick={cancelProposal}
          >
            {cancel.isPending ? "Cancelling proposal..." : "Cancel proposal"}
          </button>
          {cancel.isError && (
            <p role="alert">
              Failed to cancel proposal: {errorMessage(cancel.error)}
            </p>
          )}
        </section>
      )}
      <p>
        Step 5 stops at human review. No promotion, rollback, revision creation,
        activation, Task, Run, approval, or tool execution is available here.
      </p>
    </section>
  );
}
