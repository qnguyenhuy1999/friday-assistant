import type {
  Agent,
  JsonValue,
  Workflow,
  WorkflowRevision,
} from "@friday/contracts";
import { useAgents } from "../hooks/use-agents";
import {
  useActivateWorkflowRevision,
  useWorkflow,
  useWorkflowLifecycle,
  useWorkflowRevisions,
} from "../hooks/use-workflows";
import { WorkflowRevisionEditor } from "./workflow-revision-editor";

function formatTime(value: string): string {
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.valueOf()) ? value : timestamp.toLocaleString();
}

function prettyJson(value: JsonValue): string {
  return JSON.stringify(value, null, 2);
}

function targetLabel(agentId: string, agents: Agent[]): string {
  const agent = agents.find((item) => item.id === agentId);
  return agent ? `${agent.display_name} · ${agent.key}` : agentId;
}

function RevisionInspection({
  revision,
  workflow,
  agents,
  activate,
  activationPending,
}: {
  revision: WorkflowRevision;
  workflow: Workflow;
  agents: Agent[];
  activate: (revision: WorkflowRevision) => void;
  activationPending: boolean;
}) {
  const isSelectedRevision = revision.id === workflow.active_revision_id;
  const isEffectivelyActive =
    workflow.status === "active" && isSelectedRevision;
  return (
    <li>
      <article aria-label={`Workflow revision v${revision.version}`}>
        <h4>
          v{revision.version}
          {isEffectivelyActive
            ? " — active"
            : isSelectedRevision
              ? " — selected"
              : ""}
        </h4>
        <dl>
          <dt>Revision ID</dt>
          <dd>{revision.id}</dd>
          <dt>Content SHA-256</dt>
          <dd>{revision.content_sha256}</dd>
          <dt>Source kind</dt>
          <dd>{revision.source_kind}</dd>
          <dt>Created</dt>
          <dd>{formatTime(revision.created_at)}</dd>
        </dl>
        {workflow.status !== "archived" && !isEffectivelyActive && (
          <button
            type="button"
            disabled={activationPending}
            onClick={() => activate(revision)}
          >
            Activate v{revision.version}
          </button>
        )}
        <h5>Nodes</h5>
        <ul>
          {revision.nodes.map((node) => (
            <li key={node.id}>
              <strong>{node.node_key}</strong>
              <dl>
                <dt>Target Agent</dt>
                <dd>
                  {targetLabel(node.target_agent_id, agents)} (
                  {node.target_agent_id})
                </dd>
                <dt>Objective</dt>
                <dd>{node.objective}</dd>
                <dt>Input payload</dt>
                <dd>
                  <pre>{prettyJson(node.input_payload)}</pre>
                </dd>
                <dt>Expected output contract</dt>
                <dd>{node.expected_output_contract}</dd>
              </dl>
            </li>
          ))}
        </ul>
        <h5>Edges</h5>
        {revision.edges.length === 0 ? (
          <p>No edges.</p>
        ) : (
          <ul>
            {revision.edges.map((edge) => (
              <li key={edge.id}>
                {edge.from} → {edge.to}
              </li>
            ))}
          </ul>
        )}
      </article>
    </li>
  );
}

export function WorkflowDetailPage({
  workflowId,
  onBack,
}: {
  workflowId: string;
  onBack: () => void;
}) {
  const workflow = useWorkflow(workflowId);
  const revisions = useWorkflowRevisions(workflowId);
  const agents = useAgents();
  const activateRevision = useActivateWorkflowRevision(workflowId);
  const lifecycle = useWorkflowLifecycle(workflowId);

  function activate(revision: WorkflowRevision) {
    if (
      window.confirm(
        `Activate Workflow revision v${revision.version} for future Workflow resolution? Existing frozen WorkflowExecutions keep their revision.`,
      )
    )
      activateRevision.mutate(revision.id);
  }

  function changeLifecycle(action: "disable" | "archive") {
    const label = action === "disable" ? "disable" : "archive";
    if (window.confirm(`Are you sure you want to ${label} this Workflow?`))
      lifecycle.mutate(action);
  }

  if (workflow.isLoading) return <p>Loading Workflow…</p>;
  if (workflow.isError || !workflow.data)
    return <p role="alert">Failed to load Workflow.</p>;
  const current = workflow.data;
  const agentItems = agents.data?.pages.flatMap((page) => page.items) ?? [];
  const revisionItems = revisions.data?.pages.flatMap((page) => page) ?? [];
  return (
    <section>
      <button type="button" onClick={onBack}>
        Back to Workflows
      </button>
      <h2>{current.display_name}</h2>
      <dl>
        <dt>Workflow ID</dt>
        <dd>{current.id}</dd>
        <dt>Key</dt>
        <dd>{current.key}</dd>
        <dt>Description</dt>
        <dd>{current.description || "No description"}</dd>
        <dt>Lifecycle status</dt>
        <dd>{current.status}</dd>
        <dt>Selected revision pointer</dt>
        <dd>{current.active_revision_id ?? "No selected revision"}</dd>
        <dt>Created</dt>
        <dd>{formatTime(current.created_at)}</dd>
        <dt>Updated</dt>
        <dd>{formatTime(current.updated_at)}</dd>
      </dl>
      {current.status !== "archived" && (
        <p>
          {current.status === "active" && (
            <button
              type="button"
              disabled={lifecycle.isPending}
              onClick={() => changeLifecycle("disable")}
            >
              Disable Workflow
            </button>
          )}{" "}
          <button
            type="button"
            disabled={lifecycle.isPending}
            onClick={() => changeLifecycle("archive")}
          >
            Archive Workflow
          </button>
        </p>
      )}
      {lifecycle.isError && (
        <p role="alert">Failed to update Workflow lifecycle.</p>
      )}
      <h3>Immutable revision history</h3>
      <p>
        Revisions cannot be edited in place. A selected revision pointer and an
        effectively active Workflow are separate states. History is loaded in
        bounded newest-first pages.
      </p>
      {revisions.isLoading && <p>Loading Workflow revisions…</p>}
      {revisions.isError && (
        <p role="alert">Failed to load Workflow revisions.</p>
      )}
      {!revisions.isLoading && !revisions.isError && revisionItems.length === 0 && (
        <p>No revisions yet.</p>
      )}
      <ol aria-label="Workflow revision history">
        {revisionItems.map((revision) => (
          <RevisionInspection
            key={revision.id}
            revision={revision}
            workflow={current}
            agents={agentItems}
            activate={activate}
            activationPending={activateRevision.isPending}
          />
        ))}
      </ol>
      {revisions.hasNextPage && (
        <button
          type="button"
          disabled={revisions.isFetchingNextPage}
          onClick={() => void revisions.fetchNextPage()}
        >
          {revisions.isFetchingNextPage
            ? "Loading older revisions…"
            : "Load older revisions"}
        </button>
      )}
      {activateRevision.isError && (
        <p role="alert">Failed to activate Workflow revision.</p>
      )}
      <p>
        Activation changes future Workflow resolution only. It does not rewrite
        a frozen WorkflowExecution or existing child Run Agent resolution.
      </p>
      {current.status === "archived" ? (
        <p>
          This Workflow is archived and read-only. Its immutable revisions and
          DAG provenance remain available for inspection.
        </p>
      ) : (
        <WorkflowRevisionEditor
          workflowId={workflowId}
          agents={agentItems}
          agentsLoading={agents.isLoading}
          agentsError={agents.isError}
          agentsHasNextPage={agents.hasNextPage}
          agentsLoadingMore={agents.isFetchingNextPage}
          onLoadMoreAgents={() => void agents.fetchNextPage()}
        />
      )}
    </section>
  );
}
