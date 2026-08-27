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
import { useRunAgent } from "../hooks/use-run-agent";
export function RunDetailPage({
  runId,
  onViewApprovals,
}: {
  runId: string;
  onViewApprovals: () => void;
}) {
  const { data: run, isLoading, isError } = useRun(runId);
  const steps = useRunSteps(runId);
  const invocations = useRunToolInvocations(runId);
  const artifacts = useRunArtifacts(runId);
  const approvals = useRunApprovals(runId);
  const eventStream = useRunEventStream(runId);
  const agentResolution = useRunAgent(runId);
  if (isLoading) return <p>Loading run…</p>;
  if (isError || !run) return <p role="alert">Failed to load run.</p>;
  const pending = approvals.data?.items.filter(
    (a) => a.status === "pending",
  ).length;
  const mustExposeApprovals = run.status === "waiting_for_approval";
  return (
    <section>
      <h2>Run {run.id}</h2>
      <p>Status: {run.status}</p>
      <h3>Frozen Agent provenance</h3>
      {agentResolution.isLoading && <p>Loading Agent provenance…</p>}
      {agentResolution.isError && (
        <p role="alert">Failed to load Agent provenance.</p>
      )}
      {agentResolution.data &&
        (agentResolution.data.resolved ? (
          <dl>
            <dt>Agent</dt>
            <dd>{agentResolution.data.agent_id}</dd>
            <dt>Frozen revision</dt>
            <dd>{agentResolution.data.revision_id}</dd>
            <dt>Resolved at</dt>
            <dd>{agentResolution.data.resolved_at}</dd>
          </dl>
        ) : (
          <p>This Run has no resolved Agent revision.</p>
        ))}
      {approvals.isLoading && <p>Loading approval state…</p>}
      {approvals.isError && (
        <p role="alert">
          Failed to load approval state. You can still view approvals.
        </p>
      )}
      {(mustExposeApprovals || (pending ?? 0) > 0) && (
        <p role="alert">
          {pending === undefined
            ? "Approval action may be required."
            : `${pending} pending approval(s).`}{" "}
          <button onClick={onViewApprovals}>View approvals</button>
        </p>
      )}
      <h3>Steps</h3>
      {steps.isLoading && <p>Loading steps…</p>}
      {steps.isError && <p role="alert">Failed to load steps.</p>}
      <ul>
        {steps.data?.items.map((s) => (
          <li key={s.id}>
            {s.position}. {s.name} — {s.status}
          </li>
        ))}
      </ul>
      <h3>Tool invocations</h3>
      {invocations.isLoading && <p>Loading tool invocations…</p>}
      {invocations.isError ? (
        <p role="alert">Failed to load tool invocations.</p>
      ) : (
        <ToolInvocationList invocations={invocations.data?.items ?? []} />
      )}
      <h3>Artifacts</h3>
      {artifacts.isLoading && <p>Loading artifacts…</p>}
      {artifacts.isError ? (
        <p role="alert">Failed to load artifacts.</p>
      ) : (
        <ArtifactList artifacts={artifacts.data?.items ?? []} />
      )}
      <h3>Events</h3>
      {eventStream.isLoading && <p>Loading event history…</p>}
      {eventStream.isDegraded && (
        <p role="alert">
          Event stream is degraded; refreshing event history periodically.
        </p>
      )}
      <EventTimeline events={eventStream.events} />
      {isTerminalRunStatus(run.status) && (
        <FinalResultPanel run={run} events={eventStream.events} />
      )}
    </section>
  );
}
