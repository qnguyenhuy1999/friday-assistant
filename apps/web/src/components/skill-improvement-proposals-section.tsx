import type { ImprovementProposal } from "@friday/contracts";
import { useSkillImprovementProposals } from "../hooks/use-skill-improvement";

function formatTime(value: string) {
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.valueOf()) ? value : timestamp.toLocaleString();
}

function ProposalSummary({
  proposal,
  onViewProposal,
}: {
  proposal: ImprovementProposal;
  onViewProposal: (proposalId: string) => void;
}) {
  return (
    <li>
      <article aria-label={`Improvement proposal ${proposal.id}`}>
        <h4>{proposal.id}</h4>
        <dl>
          <dt>Proposal ID</dt>
          <dd>{proposal.id}</dd>
          <dt>Base revision ID</dt>
          <dd>{proposal.base_revision_id}</dd>
          <dt>Status</dt>
          <dd>{proposal.status}</dd>
          <dt>Evidence snapshot ID</dt>
          <dd>{proposal.evidence_snapshot_id}</dd>
          <dt>Evidence snapshot SHA-256</dt>
          <dd>{proposal.evidence_snapshot_hash}</dd>
          <dt>Candidate SHA-256</dt>
          <dd>{proposal.proposed_content_sha256}</dd>
          <dt>Generator version</dt>
          <dd>{proposal.generator_version}</dd>
          <dt>Created at</dt>
          <dd>{formatTime(proposal.created_at)}</dd>
        </dl>
        <button type="button" onClick={() => onViewProposal(proposal.id)}>
          Inspect proposal
        </button>
      </article>
    </li>
  );
}

export function SkillImprovementProposalsSection({
  skillId,
  onViewProposal,
}: {
  skillId: string;
  onViewProposal: (proposalId: string) => void;
}) {
  const proposals = useSkillImprovementProposals(skillId);
  const items = proposals.data ?? [];
  const provenanceMismatch = items.some(
    (proposal) => proposal.skill_id !== skillId,
  );

  return (
    <section aria-labelledby="skill-improvement-proposals-heading">
      <h3 id="skill-improvement-proposals-heading">Improvement proposals</h3>
      <p>
        Proposals are inert candidate suggestions. They are not Skill revisions,
        are not used by production Runs, and stop here for human review.
      </p>
      <button
        type="button"
        disabled={proposals.isFetching}
        onClick={() => void proposals.refetch()}
      >
        {proposals.isFetching ? "Refreshing proposals..." : "Refresh proposals"}
      </button>
      {proposals.isLoading && <p>Loading improvement proposals...</p>}
      {proposals.isError && (
        <p role="alert">Failed to load improvement proposals.</p>
      )}
      {!proposals.isLoading && !proposals.isError && provenanceMismatch && (
        <p role="alert">
          Improvement proposal provenance could not be verified.
        </p>
      )}
      {!proposals.isLoading &&
        !proposals.isError &&
        !provenanceMismatch &&
        items.length === 0 && <p>No improvement proposals yet.</p>}
      {!proposals.isLoading &&
        !proposals.isError &&
        !provenanceMismatch &&
        items.length > 0 && (
          <ol aria-label="Skill improvement proposals">
            {items.map((proposal) => (
              <ProposalSummary
                key={proposal.id}
                proposal={proposal}
                onViewProposal={onViewProposal}
              />
            ))}
          </ol>
        )}
    </section>
  );
}
