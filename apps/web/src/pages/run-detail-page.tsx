import { ArtifactList } from "../components/artifact-list";
import { EventTimeline } from "../components/event-timeline";
import { FinalResultPanel } from "../components/final-result-panel";
import { ToolInvocationList } from "../components/tool-invocation-list";
import { useRunArtifacts } from "../hooks/use-artifacts";
import { useRunApprovals } from "../hooks/use-approvals";
import { isTerminalRunStatus, useRun } from "../hooks/use-run";
import { useRunEventStream } from "../hooks/use-run-event-stream";
import { useRunSteps } from "../hooks/use-run-steps";
import { useRunToolInvocations } from "../hooks/use-tool-invocations";
export function RunDetailPage({
  runId,
  onViewApprovals,
}: {
  runId: string;
  onViewApprovals: () => void;
}) {
  const { data: run, isLoading, isError } = useRun(runId);
  const { data: steps } = useRunSteps(runId);
  const { data: invocations } = useRunToolInvocations(runId);
  const { data: artifacts } = useRunArtifacts(runId);
  const { data: approvals } = useRunApprovals(runId);
  const events = useRunEventStream(runId);
  if (isLoading) return <p>Loading run…</p>;
  if (isError || !run) return <p role="alert">Failed to load run.</p>;
  const pending =
    approvals?.items.filter((a) => a.status === "pending").length ?? 0;
  return (
    <section>
      <h2>Run {run.id}</h2>
      <p>Status: {run.status}</p>
      {pending > 0 && (
        <p role="alert">
          {pending} pending approval(s).{" "}
          <button onClick={onViewApprovals}>View approvals</button>
        </p>
      )}
      <h3>Steps</h3>
      <ul>
        {steps?.items.map((s) => (
          <li key={s.id}>
            {s.position}. {s.name} — {s.status}
          </li>
        ))}
      </ul>
      <h3>Tool invocations</h3>
      <ToolInvocationList invocations={invocations?.items ?? []} />
      <h3>Artifacts</h3>
      <ArtifactList artifacts={artifacts?.items ?? []} />
      <h3>Events</h3>
      <EventTimeline events={events} />
      {isTerminalRunStatus(run.status) && <FinalResultPanel run={run} />}
    </section>
  );
}
