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
import {
  isMissingWorkflowExecution,
  useRunWorkflow,
  useRunWorkflowNodes,
} from "../hooks/use-run-workflow";

function prettyJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

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
  const workflowResolution = useRunWorkflow(runId);
  const workflowNodes = useRunWorkflowNodes(
    runId,
    workflowResolution.data !== undefined,
  );
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
      <h3>Frozen Workflow provenance</h3>
      {workflowResolution.isLoading && <p>Loading Workflow provenance…</p>}
      {workflowResolution.isError &&
        (isMissingWorkflowExecution(workflowResolution.error) ? (
          <p>This Run is not backed by a Workflow execution.</p>
        ) : (
          <p role="alert">Failed to load Workflow provenance.</p>
        ))}
      {workflowResolution.data && (
        <>
          <dl>
            <dt>Workflow execution ID</dt>
            <dd>{workflowResolution.data.workflow_execution_id}</dd>
            <dt>Workflow ID</dt>
            <dd>{workflowResolution.data.workflow_id}</dd>
            <dt>Frozen Workflow revision</dt>
            <dd>{workflowResolution.data.workflow_revision_id}</dd>
            <dt>Frozen Workflow revision SHA-256</dt>
            <dd>{workflowResolution.data.workflow_revision_sha256}</dd>
            <dt>Execution status</dt>
            <dd>{workflowResolution.data.status}</dd>
          </dl>
          {workflowResolution.data.failure_message && (
            <p role="alert">
              {workflowResolution.data.failure_code}:{" "}
              {workflowResolution.data.failure_message}
            </p>
          )}
          <h4>Workflow node execution provenance</h4>
          {workflowNodes.isLoading && <p>Loading Workflow node provenance…</p>}
          {workflowNodes.isError && (
            <p role="alert">Failed to load Workflow node provenance.</p>
          )}
          <ul>
            {workflowNodes.data?.map((node) => (
              <li key={node.node_execution_id}>
                <strong>{node.node_key}</strong> — {node.status}
                <dl>
                  <dt>Target Agent ID</dt>
                  <dd>{node.target_agent_id}</dd>
                  <dt>Frozen target Agent revision ID</dt>
                  <dd>{node.target_agent_revision_id}</dd>
                  <dt>Frozen target Agent revision SHA-256</dt>
                  <dd>{node.target_agent_revision_sha256}</dd>
                  {node.child_task_id && (
                    <>
                      <dt>Child Task ID</dt>
                      <dd>{node.child_task_id}</dd>
                    </>
                  )}
                  {node.child_run_id && (
                    <>
                      <dt>Child Run ID</dt>
                      <dd>{node.child_run_id}</dd>
                    </>
                  )}
                  {node.child_execution_id && (
                    <>
                      <dt>Child execution ID</dt>
                      <dd>{node.child_execution_id}</dd>
                    </>
                  )}
                  {node.result_payload !== null && (
                    <>
                      <dt>Result payload</dt>
                      <dd>
                        <pre>{prettyJson(node.result_payload)}</pre>
                      </dd>
                    </>
                  )}
                  {node.failure_message && (
                    <>
                      <dt>Failure</dt>
                      <dd>
                        {node.failure_code}: {node.failure_message}
                      </dd>
                    </>
                  )}
                </dl>
              </li>
            ))}
          </ul>
        </>
      )}
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
