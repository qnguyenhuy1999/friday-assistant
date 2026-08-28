import type { Agent, Failure, JsonValue, Workflow } from "@friday/contracts";
import { useEffect, useState } from "react";
import { useAgent, useAgents } from "../hooks/use-agents";
import {
  useBindTaskWorkflow,
  useClearTaskAgent,
  usePutTaskAgent,
  useStartRun,
  useTask,
  useTaskAgentBinding,
  useTaskWorkflowBinding,
  useUnbindTaskWorkflow,
} from "../hooks/use-tasks";
import { useWorkflow, useWorkflows } from "../hooks/use-workflows";

function formatTime(value: string): string {
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.valueOf()) ? value : timestamp.toLocaleString();
}

function prettyJson(value: JsonValue): string {
  return JSON.stringify(value, null, 2) ?? "None";
}

function agentBindingReason(agent: Agent): string | null {
  if (agent.status === "archived") return "Archived — cannot be newly bound.";
  if (agent.status === "disabled") return "Disabled — cannot be newly bound.";
  if (agent.active_revision_id === null)
    return "No selected revision — cannot be newly bound.";
  return null;
}

function workflowBindingReason(workflow: Workflow): string | null {
  return workflow.status === "archived"
    ? "Archived — cannot be newly bound."
    : null;
}

function workflowReadinessWarning(workflow: Workflow): string | null {
  if (workflow.status === "archived")
    return "This Workflow is archived and cannot be newly bound. A future unresolved Run cannot resolve it until the Workflow becomes active with a selected revision.";
  if (workflow.status !== "active" || workflow.active_revision_id === null)
    return "Binding is allowed, but a future unresolved Run may fail Workflow resolution until this Workflow becomes active with a selected revision.";
  return null;
}

function agentOptionLabel(agent: Agent): string {
  const reason = agentBindingReason(agent);
  return [
    agent.display_name,
    agent.key,
    agent.status,
    `selected revision: ${agent.active_revision_id ?? "none"}`,
    reason,
  ]
    .filter(Boolean)
    .join(" · ");
}

function workflowOptionLabel(workflow: Workflow): string {
  const bindingReason = workflowBindingReason(workflow);
  const warning = workflowReadinessWarning(workflow);
  return [
    workflow.display_name,
    workflow.key,
    workflow.status,
    `selected revision: ${workflow.active_revision_id ?? "none"}`,
    bindingReason ?? (warning ? "launch-readiness warning" : null),
  ]
    .filter(Boolean)
    .join(" · ");
}

function valueOrUnavailable(value: string | null | undefined): string {
  return value ?? "Unavailable";
}

function FailureDetails({ failure }: { failure: Failure }) {
  return (
    <section>
      <h3>Failure information</h3>
      <dl>
        <dt>Code</dt>
        <dd>{failure.code}</dd>
        <dt>Message</dt>
        <dd>{failure.message}</dd>
        <dt>Cause</dt>
        <dd>{failure.cause}</dd>
        <dt>Retryable</dt>
        <dd>{failure.retryable ? "Yes" : "No"}</dd>
        <dt>Details</dt>
        <dd>
          <pre>{prettyJson(failure.details)}</pre>
        </dd>
      </dl>
    </section>
  );
}

export function TaskDetailPage({
  taskId,
  onBack,
  onRunStarted,
  onViewSchedules,
}: {
  taskId: string;
  onBack: () => void;
  onRunStarted: (runId: string) => void;
  onViewSchedules: (taskId: string) => void;
}) {
  const task = useTask(taskId);
  const agentBinding = useTaskAgentBinding(taskId);
  const workflowBinding = useTaskWorkflowBinding(taskId);
  const agents = useAgents();
  const workflows = useWorkflows();
  const agentBindingData = agentBinding.data;
  const workflowBindingData = workflowBinding.data;
  const boundAgentId = agentBindingData?.agent_id ?? null;
  const boundWorkflowId = workflowBindingData?.workflow_id ?? null;
  const boundAgent = useAgent(boundAgentId);
  const boundWorkflow = useWorkflow(boundWorkflowId);
  const putAgent = usePutTaskAgent(taskId);
  const clearAgent = useClearTaskAgent(taskId);
  const bindWorkflow = useBindTaskWorkflow(taskId);
  const unbindWorkflow = useUnbindTaskWorkflow(taskId);
  const startRun = useStartRun();
  const [selectedAgentId, setSelectedAgentId] = useState("");
  const [selectedWorkflowId, setSelectedWorkflowId] = useState("");

  useEffect(() => {
    if (agentBindingData !== undefined)
      setSelectedAgentId(agentBindingData?.agent_id ?? "");
  }, [agentBindingData]);

  useEffect(() => {
    if (workflowBindingData !== undefined)
      setSelectedWorkflowId(workflowBindingData?.workflow_id ?? "");
  }, [workflowBindingData]);

  if (task.isLoading) return <p>Loading task…</p>;
  if (task.isError || !task.data)
    return <p role="alert">Failed to load task.</p>;

  const currentTask = task.data;
  const agentItems = agents.data?.pages.flatMap((page) => page.items) ?? [];
  const workflowItems =
    workflows.data?.pages.flatMap((page) => page.items) ?? [];
  const currentAgent =
    boundAgent.data ?? agentItems.find((agent) => agent.id === boundAgentId);
  const currentWorkflow =
    boundWorkflow.data ??
    workflowItems.find((workflow) => workflow.id === boundWorkflowId);
  const agentOptions =
    currentAgent && boundAgentId
      ? agentItems.some((agent) => agent.id === boundAgentId)
        ? agentItems
        : [currentAgent, ...agentItems]
      : agentItems;
  const workflowOptions =
    currentWorkflow && boundWorkflowId
      ? workflowItems.some((workflow) => workflow.id === boundWorkflowId)
        ? workflowItems
        : [currentWorkflow, ...workflowItems]
      : workflowItems;
  const selectedAgent = agentOptions.find(
    (agent) => agent.id === selectedAgentId,
  );
  const selectedWorkflow = workflowOptions.find(
    (workflow) => workflow.id === selectedWorkflowId,
  );
  const bindingLoadError = agentBinding.isError || workflowBinding.isError;
  const bindingsLoading = agentBinding.isLoading || workflowBinding.isLoading;
  const inconsistent = boundAgentId !== null && boundWorkflowId !== null;
  const targetDetailsLoading =
    (boundAgentId !== null && boundAgent.isLoading) ||
    (boundWorkflowId !== null && boundWorkflow.isLoading);
  const targetDetailsError =
    (boundAgentId !== null && boundAgent.isError) ||
    (boundWorkflowId !== null && boundWorkflow.isError);
  const bindingsReady = !bindingsLoading && !bindingLoadError;
  const mutationPending =
    putAgent.isPending ||
    clearAgent.isPending ||
    bindWorkflow.isPending ||
    unbindWorkflow.isPending;
  const canStartRun =
    bindingsReady &&
    !inconsistent &&
    !targetDetailsLoading &&
    !targetDetailsError &&
    !mutationPending &&
    !startRun.isPending;

  function bindSelectedAgent() {
    if (selectedAgent && agentBindingReason(selectedAgent) === null)
      putAgent.mutate(selectedAgent.id);
  }

  function bindSelectedWorkflow() {
    if (selectedWorkflow && workflowBindingReason(selectedWorkflow) === null)
      bindWorkflow.mutate(selectedWorkflow.id);
  }

  function start() {
    startRun.mutate(taskId, {
      onSuccess: (result) => onRunStarted(result.run_id),
    });
  }

  return (
    <section aria-labelledby="task-detail-title">
      <button type="button" onClick={onBack}>
        Back to Tasks
      </button>
      <h2 id="task-detail-title">{currentTask.title}</h2>
      <dl>
        <dt>Task ID</dt>
        <dd>{currentTask.id}</dd>
        <dt>Description</dt>
        <dd>{currentTask.description || "No description"}</dd>
        <dt>Lifecycle status</dt>
        <dd>{currentTask.status}</dd>
        <dt>Created</dt>
        <dd>{formatTime(currentTask.created_at)}</dd>
      </dl>
      {currentTask.failure && <FailureDetails failure={currentTask.failure} />}
      <p>
        Task Detail shows mutable future routing configuration. It does not
        replace the frozen execution provenance shown by Run Detail.
      </p>
      <p>
        Binding changes affect unresolved Runs, including queued Runs that have
        not yet frozen their Agent or Workflow resolution. Once a worker freezes
        a Run resolution, that exact provenance is immutable.
      </p>

      <h3>Current execution target</h3>
      {bindingsLoading && <p>Loading execution target…</p>}
      {bindingLoadError && (
        <p role="alert">
          Failed to load execution target bindings. Changes and Run start are
          unavailable until the Task bindings can be verified.
        </p>
      )}
      {bindingsReady && inconsistent && (
        <article role="alert">
          <h4>Inconsistent</h4>
          <p>
            Task execution-target state is inconsistent. Both Agent and Workflow
            bindings are present.
          </p>
          <p>
            Friday rejected this state elsewhere, so no binding repair or Run
            start is available from this page.
          </p>
        </article>
      )}
      {bindingsReady &&
        !inconsistent &&
        boundAgentId === null &&
        boundWorkflowId === null && (
          <article>
            <h4>Default Friday runtime</h4>
            <p>
              No Agent or Workflow binding is configured. Friday&apos;s existing
              default processing behavior applies.
            </p>
          </article>
        )}
      {bindingsReady && !inconsistent && boundAgentId !== null && (
        <article>
          <h4>Agent</h4>
          <dl>
            <dt>Agent display name</dt>
            <dd>{valueOrUnavailable(currentAgent?.display_name)}</dd>
            <dt>Agent key</dt>
            <dd>{valueOrUnavailable(currentAgent?.key)}</dd>
            <dt>Agent ID</dt>
            <dd>{boundAgentId}</dd>
            <dt>Agent lifecycle status</dt>
            <dd>{valueOrUnavailable(currentAgent?.status)}</dd>
            <dt>Selected revision ID</dt>
            <dd>{valueOrUnavailable(currentAgent?.active_revision_id)}</dd>
          </dl>
          {targetDetailsLoading && <p>Loading Agent details…</p>}
          {targetDetailsError && (
            <p role="alert">Failed to load the bound Agent details.</p>
          )}
          {currentAgent && agentBindingReason(currentAgent) !== null && (
            <p role="alert">
              Launch-readiness warning: {agentBindingReason(currentAgent)} A
              future unresolved Run may fail Agent resolution while this Agent
              remains unavailable.
            </p>
          )}
        </article>
      )}
      {bindingsReady && !inconsistent && boundWorkflowId !== null && (
        <article>
          <h4>Workflow</h4>
          <dl>
            <dt>Workflow display name</dt>
            <dd>{valueOrUnavailable(currentWorkflow?.display_name)}</dd>
            <dt>Workflow key</dt>
            <dd>{valueOrUnavailable(currentWorkflow?.key)}</dd>
            <dt>Workflow ID</dt>
            <dd>{boundWorkflowId}</dd>
            <dt>Workflow lifecycle status</dt>
            <dd>{valueOrUnavailable(currentWorkflow?.status)}</dd>
            <dt>Selected revision ID</dt>
            <dd>{valueOrUnavailable(currentWorkflow?.active_revision_id)}</dd>
          </dl>
          {targetDetailsLoading && <p>Loading Workflow details…</p>}
          {targetDetailsError && (
            <p role="alert">Failed to load the bound Workflow details.</p>
          )}
          {currentWorkflow && workflowReadinessWarning(currentWorkflow) && (
            <p role="alert">
              Launch-readiness warning:{" "}
              {workflowReadinessWarning(currentWorkflow)}
            </p>
          )}
        </article>
      )}

      {bindingsReady && !inconsistent && (
        <>
          <h3>Execution preview</h3>
          {boundAgentId === null && boundWorkflowId === null && (
            <p>
              Execution target: <strong>Default Friday runtime</strong>
            </p>
          )}
          {boundAgentId !== null && (
            <p>
              Execution target: <strong>Agent</strong> —{" "}
              {valueOrUnavailable(currentAgent?.display_name)}
              <br />
              Selected revision:{" "}
              {valueOrUnavailable(currentAgent?.active_revision_id)}
            </p>
          )}
          {boundWorkflowId !== null && (
            <p>
              Execution target: <strong>Workflow</strong> —{" "}
              {valueOrUnavailable(currentWorkflow?.display_name)}
              <br />
              Selected revision:{" "}
              {valueOrUnavailable(currentWorkflow?.active_revision_id)}
            </p>
          )}
          <p>
            Starting a Run queues it for Friday&apos;s worker. The worker owns
            Agent or Workflow resolution; the browser never chooses an execution
            path.
          </p>
          <button type="button" disabled={!canStartRun} onClick={start}>
            {startRun.isPending ? "Starting Run…" : "Start Run"}
          </button>
          {startRun.isError && (
            <p role="alert">
              Failed to start the Run. The server rejected this Task, and no Run
              was opened.
            </p>
          )}
        </>
      )}

      {bindingsReady && !inconsistent && (
        <section>
          <h3>Manage execution target</h3>
          <p>
            Agent and Workflow bindings are mutually exclusive. Cross-kind
            changes require a separate clear operation first; this page never
            hides a two-request switch behind one button.
          </p>

          <h4>Agent binding</h4>
          {boundWorkflowId !== null && (
            <p>Clear Workflow binding before binding an Agent.</p>
          )}
          {agents.isLoading && <p>Loading Agents…</p>}
          {agents.isError && (
            <p role="alert">
              Failed to load Agents. Agent binding controls are unavailable.
            </p>
          )}
          <label htmlFor="task-agent-target">Agent target</label>
          <select
            id="task-agent-target"
            value={selectedAgentId}
            disabled={
              agents.isError ||
              Boolean(boundWorkflowId) ||
              mutationPending ||
              agents.isLoading
            }
            onChange={(event) => setSelectedAgentId(event.target.value)}
          >
            <option value="">Select an Agent</option>
            {agentOptions.map((agent) => (
              <option
                key={agent.id}
                value={agent.id}
                disabled={agentBindingReason(agent) !== null}
              >
                {agentOptionLabel(agent)}
              </option>
            ))}
          </select>
          <button
            type="button"
            disabled={
              agents.isError ||
              agents.isLoading ||
              Boolean(boundWorkflowId) ||
              mutationPending ||
              selectedAgent === undefined ||
              agentBindingReason(selectedAgent) !== null
            }
            onClick={bindSelectedAgent}
          >
            Bind selected Agent
          </button>
          {boundAgentId !== null && (
            <button
              type="button"
              disabled={mutationPending}
              onClick={() => clearAgent.mutate()}
            >
              Clear Agent binding
            </button>
          )}
          {putAgent.isError && (
            <p role="alert">
              Failed to bind the Agent. The server rejected the change; no other
              binding was modified.
            </p>
          )}
          {clearAgent.isError && (
            <p role="alert">
              Failed to clear the Agent binding. The Task was not changed by
              this request.
            </p>
          )}

          <h4>Workflow binding</h4>
          {boundAgentId !== null && (
            <p>Clear Agent binding before binding a Workflow.</p>
          )}
          {workflows.isLoading && <p>Loading Workflows…</p>}
          {workflows.isError && (
            <p role="alert">
              Failed to load Workflows. Workflow binding controls are
              unavailable.
            </p>
          )}
          <label htmlFor="task-workflow-target">Workflow target</label>
          <select
            id="task-workflow-target"
            value={selectedWorkflowId}
            disabled={
              workflows.isError ||
              Boolean(boundAgentId) ||
              mutationPending ||
              workflows.isLoading
            }
            onChange={(event) => setSelectedWorkflowId(event.target.value)}
          >
            <option value="">Select a Workflow</option>
            {workflowOptions.map((workflow) => (
              <option
                key={workflow.id}
                value={workflow.id}
                disabled={workflowBindingReason(workflow) !== null}
              >
                {workflowOptionLabel(workflow)}
              </option>
            ))}
          </select>
          {selectedWorkflow && workflowReadinessWarning(selectedWorkflow) && (
            <p>
              Launch-readiness warning:{" "}
              {workflowReadinessWarning(selectedWorkflow)}
            </p>
          )}
          <button
            type="button"
            disabled={
              workflows.isError ||
              workflows.isLoading ||
              Boolean(boundAgentId) ||
              mutationPending ||
              selectedWorkflow === undefined ||
              workflowBindingReason(selectedWorkflow) !== null
            }
            onClick={bindSelectedWorkflow}
          >
            Bind selected Workflow
          </button>
          {boundWorkflowId !== null && (
            <button
              type="button"
              disabled={mutationPending}
              onClick={() => unbindWorkflow.mutate()}
            >
              Clear Workflow binding
            </button>
          )}
          {bindWorkflow.isError && (
            <p role="alert">
              Failed to bind the Workflow. The server rejected the change; no
              other binding was modified.
            </p>
          )}
          {unbindWorkflow.isError && (
            <p role="alert">
              Failed to clear the Workflow binding. The Task was not changed by
              this request.
            </p>
          )}
          {agents.hasNextPage && (
            <button
              type="button"
              disabled={agents.isFetchingNextPage}
              onClick={() => void agents.fetchNextPage()}
            >
              {agents.isFetchingNextPage
                ? "Loading more Agents…"
                : "Load more Agents"}
            </button>
          )}
          {workflows.hasNextPage && (
            <button
              type="button"
              disabled={workflows.isFetchingNextPage}
              onClick={() => void workflows.fetchNextPage()}
            >
              {workflows.isFetchingNextPage
                ? "Loading more Workflows…"
                : "Load more Workflows"}
            </button>
          )}
        </section>
      )}

      <h3>Schedules</h3>
      <button type="button" onClick={() => onViewSchedules(taskId)}>
        View Schedules
      </button>
    </section>
  );
}
