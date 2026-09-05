import type { SkillUsageRecord } from "@friday/contracts";
import { useSkillUsage } from "../hooks/use-skills";

const EXPOSED_USAGE_RECORD_LIMIT = 100;

function formatTime(value: string | null): string {
  if (value === null) return "Not recorded";
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.valueOf()) ? value : timestamp.toLocaleString();
}

function formatFailureCode(value: string | null): string {
  return value ?? "None";
}

function formatDuration(value: number | null): string {
  return value === null ? "Not recorded" : `${value} ms`;
}

function UsageEvidenceRecord({
  record,
  onViewRun,
}: {
  record: SkillUsageRecord;
  onViewRun: (runId: string) => void;
}) {
  return (
    <li>
      <article aria-label={`Usage evidence for Run ${record.run_id}`}>
        <h4>Run {record.run_id}</h4>
        <dl>
          <dt>Evidence record ID</dt>
          <dd>{record.id}</dd>
          <dt>Run ID</dt>
          <dd>{record.run_id}</dd>
          <dt>Task ID</dt>
          <dd>{record.task_id}</dd>
          <dt>Skill ID</dt>
          <dd>{record.skill_id}</dd>
          <dt>Frozen Skill revision ID</dt>
          <dd>{record.revision_id}</dd>
          <dt>Skill position</dt>
          <dd>{record.position}</dd>
          <dt>Resolution ID</dt>
          <dd>{record.resolution_id}</dd>
          <dt>Execution ID</dt>
          <dd>{record.execution_id}</dd>
          <dt>Attempt number</dt>
          <dd>{record.attempt_number}</dd>
          <dt>Outcome</dt>
          <dd>{record.outcome}</dd>
          <dt>Failure code</dt>
          <dd>{formatFailureCode(record.failure_code)}</dd>
          <dt>Started at</dt>
          <dd>{formatTime(record.started_at)}</dd>
          <dt>Completed at</dt>
          <dd>{formatTime(record.completed_at)}</dd>
          <dt>Duration</dt>
          <dd>{formatDuration(record.duration_ms)}</dd>
          <dt>Tool call count</dt>
          <dd>{record.tool_call_count}</dd>
          <dt>Approval count</dt>
          <dd>{record.approval_count}</dd>
          <dt>Evidence created at</dt>
          <dd>{formatTime(record.created_at)}</dd>
        </dl>
        <button type="button" onClick={() => onViewRun(record.run_id)}>
          View Run
        </button>
      </article>
    </li>
  );
}

export function SkillUsageEvidenceSection({
  skillId,
  onViewRun,
}: {
  skillId: string;
  onViewRun: (runId: string) => void;
}) {
  const usage = useSkillUsage(skillId);
  const records = usage.data ?? [];
  const hasProvenanceMismatch = records.some(
    (record) => record.skill_id !== skillId,
  );

  return (
    <section aria-labelledby="skill-usage-evidence-heading">
      <h3 id="skill-usage-evidence-heading">Recent usage evidence</h3>
      <p>
        These records are factual observations from Runs that used a frozen
        revision of this Skill. They do not prove that the Skill caused the Run
        outcome.
      </p>
      <p>
        Shows up to the {EXPOSED_USAGE_RECORD_LIMIT} most recent materialized
        usage records currently exposed by the Skill usage API.
      </p>
      {usage.isLoading && (
        <p role="status">Loading recent Skill usage evidence...</p>
      )}
      {usage.isError && (
        <p role="alert">
          Failed to load Skill usage evidence. Skill lifecycle and revision
          inspection remain available.
        </p>
      )}
      {!usage.isLoading && !usage.isError && hasProvenanceMismatch && (
        <p role="alert">Usage evidence provenance could not be verified.</p>
      )}
      {!usage.isLoading &&
        !usage.isError &&
        !hasProvenanceMismatch &&
        records.length === 0 && (
          <p>No materialized usage evidence is available for this Skill.</p>
        )}
      {!usage.isLoading &&
        !usage.isError &&
        !hasProvenanceMismatch &&
        records.length > 0 && (
          <ol aria-label="Recent Skill usage evidence">
            {records.map((record) => (
              <UsageEvidenceRecord
                key={record.id}
                record={record}
                onViewRun={onViewRun}
              />
            ))}
          </ol>
        )}
    </section>
  );
}
