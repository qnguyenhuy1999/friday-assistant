import type {
  Agent,
  CreateWorkflowRevisionBody,
  WorkflowNodeInput,
} from "@friday/contracts";
import { useRef, useState, type FormEvent } from "react";
import { useCreateWorkflowRevision } from "../hooks/use-workflows";
import {
  isValidMachineKey,
  MAX_WORKFLOW_INPUT_LENGTH,
  MAX_WORKFLOW_NODE_OBJECTIVE_LENGTH,
  MAX_WORKFLOW_OUTPUT_CONTRACT_LENGTH,
  parseWorkflowInputPayload,
  validateWorkflowDraft,
} from "./workflow-draft";

interface DraftNode {
  id: string;
  node_key: string;
  target_agent_id: string;
  objective: string;
  input_payload: string;
  expected_output_contract: string;
}

interface DraftEdge {
  id: string;
  from: string;
  to: string;
}

function emptyNode(id: string, nodeKey: string): DraftNode {
  return {
    id,
    node_key: nodeKey,
    target_agent_id: "",
    objective: "",
    input_payload: "{}",
    expected_output_contract: "",
  };
}

function agentLabel(agent: Agent): string {
  const revision = agent.active_revision_id
    ? `selected revision ${agent.active_revision_id}`
    : "no selected revision";
  return `${agent.display_name} · ${agent.key} · ${agent.status} · ${revision}`;
}

function previewPayload(value: string): string {
  const parsed = parseWorkflowInputPayload(value);
  return parsed.error ? value : JSON.stringify(parsed.value, null, 2);
}

export function WorkflowRevisionEditor({
  workflowId,
  agents,
  agentsLoading,
  agentsError,
  agentsHasNextPage,
  agentsLoadingMore,
  onLoadMoreAgents,
}: {
  workflowId: string;
  agents: Agent[];
  agentsLoading: boolean;
  agentsError: boolean;
  agentsHasNextPage: boolean;
  agentsLoadingMore: boolean;
  onLoadMoreAgents: () => void;
}) {
  const createRevision = useCreateWorkflowRevision(workflowId);
  const nextId = useRef(0);
  const [nodes, setNodes] = useState<DraftNode[]>([]);
  const [edges, setEdges] = useState<DraftEdge[]>([]);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [createdRevision, setCreatedRevision] = useState<number | null>(null);

  function id(prefix: string): string {
    nextId.current += 1;
    return `${prefix}-${nextId.current}`;
  }

  function addNode() {
    const used = new Set(nodes.map((node) => node.node_key));
    let suffix = nodes.length + 1;
    let nodeKey = `node-${suffix}`;
    while (used.has(nodeKey)) {
      suffix += 1;
      nodeKey = `node-${suffix}`;
    }
    setNodes((current) => [...current, emptyNode(id("node"), nodeKey)]);
    setCreatedRevision(null);
    setValidationError(null);
  }

  function updateNode(nodeId: string, patch: Partial<DraftNode>) {
    setNodes((current) =>
      current.map((node) =>
        node.id === nodeId ? { ...node, ...patch } : node,
      ),
    );
    setCreatedRevision(null);
  }

  function addEdge() {
    const first = nodes[0]?.node_key ?? "";
    const second = nodes[1]?.node_key ?? first;
    setEdges((current) => [
      ...current,
      { id: id("edge"), from: first, to: second },
    ]);
    setCreatedRevision(null);
    setValidationError(null);
  }

  function updateEdge(edgeId: string, patch: Partial<DraftEdge>) {
    setEdges((current) =>
      current.map((edge) =>
        edge.id === edgeId ? { ...edge, ...patch } : edge,
      ),
    );
    setCreatedRevision(null);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsedNodes: WorkflowNodeInput[] = [];
    for (const node of nodes) {
      const nodeKey = node.node_key.trim();
      if (!nodeKey || !isValidMachineKey(nodeKey)) {
        setValidationError(
          "Node keys must start with a lowercase letter and use machine-readable characters.",
        );
        return;
      }
      const payload = parseWorkflowInputPayload(node.input_payload);
      if (payload.error || payload.value === undefined) {
        setValidationError(`Node “${nodeKey}”: ${payload.error}`);
        return;
      }
      parsedNodes.push({
        node_key: nodeKey,
        target_agent_id: node.target_agent_id,
        objective: node.objective.trim(),
        input_payload: payload.value,
        expected_output_contract: node.expected_output_contract.trim(),
      });
    }
    const draftError = validateWorkflowDraft(parsedNodes, edges);
    if (draftError) {
      setValidationError(draftError);
      return;
    }
    setValidationError(null);
    const body: CreateWorkflowRevisionBody = {
      nodes: parsedNodes,
      edges: edges.map(({ from, to }) => ({ from, to })),
      source_kind: "operator",
    };
    createRevision.mutate(body, {
      onSuccess: (revision) => {
        setCreatedRevision(revision.version);
        setNodes([]);
        setEdges([]);
      },
    });
  }

  return (
    <section>
      <h3>Create immutable revision</h3>
      <p>
        Draft values stay in the browser until submission. Creating a revision
        does not activate it automatically.
      </p>
      {agentsLoading && <p>Loading target Agents…</p>}
      {agentsError && (
        <p role="alert">
          Failed to load Agents. Target selection may be unavailable.
        </p>
      )}
      {!agentsLoading && !agentsError && agents.length === 0 && (
        <p role="alert">
          No Agents are available to target. Create an Agent before creating a
          Workflow revision.
        </p>
      )}
      <p role="status">
        Agent status and selected revision are advisory while authoring. A
        disabled Agent or an Agent without a selected revision can still be
        targeted, but future Workflow execution may fail to resolve it.
      </p>
      {agentsHasNextPage && (
        <p>
          More Agents are available. Load additional pages if the target Agent
          is not shown yet.{" "}
          <button
            type="button"
            disabled={agentsLoadingMore}
            onClick={onLoadMoreAgents}
          >
            {agentsLoadingMore ? "Loading more Agents…" : "Load more Agents"}
          </button>
        </p>
      )}
      <form onSubmit={submit} aria-label="Create workflow revision">
        <fieldset>
          <legend>Nodes</legend>
          <button type="button" onClick={addNode}>
            Add node
          </button>
          {nodes.length === 0 && (
            <p>No draft nodes yet. Add at least one node.</p>
          )}
          <ol>
            {nodes.map((node, index) => (
              <li key={node.id}>
                <strong>Draft node {index + 1}</strong>
                <label htmlFor={`${node.id}-key`}>Node key</label>
                <input
                  id={`${node.id}-key`}
                  value={node.node_key}
                  maxLength={128}
                  onChange={(event) =>
                    updateNode(node.id, { node_key: event.target.value })
                  }
                  required
                />
                <label htmlFor={`${node.id}-agent`}>Target Agent</label>
                <select
                  id={`${node.id}-agent`}
                  value={node.target_agent_id}
                  onChange={(event) =>
                    updateNode(node.id, { target_agent_id: event.target.value })
                  }
                  required
                  disabled={agentsLoading || agents.length === 0}
                >
                  <option value="">Select an Agent</option>
                  {agents.map((agent) => (
                    <option key={agent.id} value={agent.id}>
                      {agentLabel(agent)}
                    </option>
                  ))}
                </select>
                <label htmlFor={`${node.id}-objective`}>Objective</label>
                <textarea
                  id={`${node.id}-objective`}
                  value={node.objective}
                  maxLength={MAX_WORKFLOW_NODE_OBJECTIVE_LENGTH}
                  onChange={(event) =>
                    updateNode(node.id, { objective: event.target.value })
                  }
                  required
                />
                <label htmlFor={`${node.id}-input`}>Input payload (JSON)</label>
                <textarea
                  id={`${node.id}-input`}
                  value={node.input_payload}
                  maxLength={MAX_WORKFLOW_INPUT_LENGTH}
                  onChange={(event) =>
                    updateNode(node.id, { input_payload: event.target.value })
                  }
                  required
                />
                <p>Do not put credentials or secrets in Workflow payloads.</p>
                <label htmlFor={`${node.id}-output`}>
                  Expected output contract
                </label>
                <textarea
                  id={`${node.id}-output`}
                  value={node.expected_output_contract}
                  maxLength={MAX_WORKFLOW_OUTPUT_CONTRACT_LENGTH}
                  onChange={(event) =>
                    updateNode(node.id, {
                      expected_output_contract: event.target.value,
                    })
                  }
                  required
                />
                <button
                  type="button"
                  onClick={() =>
                    setNodes((current) =>
                      current.filter((item) => item.id !== node.id),
                    )
                  }
                >
                  Remove node
                </button>
              </li>
            ))}
          </ol>
        </fieldset>
        <fieldset>
          <legend>Edges</legend>
          <p>Edges reference draft nodes by their node keys.</p>
          <button
            type="button"
            onClick={addEdge}
            disabled={nodes.length === 0}
          >
            Add edge
          </button>
          {edges.length === 0 && <p>No draft edges yet.</p>}
          <ul>
            {edges.map((edge, index) => (
              <li key={edge.id}>
                <label htmlFor={`${edge.id}-from`}>From node</label>
                <select
                  id={`${edge.id}-from`}
                  value={edge.from}
                  onChange={(event) =>
                    updateEdge(edge.id, { from: event.target.value })
                  }
                >
                  <option value="">Select node</option>
                  {nodes.map((node) => (
                    <option key={node.id} value={node.node_key}>
                      {node.node_key}
                    </option>
                  ))}
                </select>
                <label htmlFor={`${edge.id}-to`}>To node</label>
                <select
                  id={`${edge.id}-to`}
                  value={edge.to}
                  onChange={(event) =>
                    updateEdge(edge.id, { to: event.target.value })
                  }
                >
                  <option value="">Select node</option>
                  {nodes.map((node) => (
                    <option key={node.id} value={node.node_key}>
                      {node.node_key}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() =>
                    setEdges((current) =>
                      current.filter((item) => item.id !== edge.id),
                    )
                  }
                >
                  Remove edge
                </button>{" "}
                <span>Edge {index + 1}</span>
              </li>
            ))}
          </ul>
        </fieldset>
        <button type="submit" disabled={createRevision.isPending}>
          Create immutable revision
        </button>
      </form>
      {validationError && <p role="alert">{validationError}</p>}
      {createRevision.isError && (
        <p role="alert">Failed to create Workflow revision.</p>
      )}
      {createdRevision !== null && (
        <p role="status">
          Created revision v{createdRevision}. It is not active until activated.
        </p>
      )}
      <h4>Revision preview</h4>
      <p>This is the deterministic definition that will be sent to Friday.</p>
      <h5>Nodes</h5>
      <ul>
        {nodes.map((node) => {
          const agent = agents.find((item) => item.id === node.target_agent_id);
          return (
            <li key={node.id}>
              <strong>{node.node_key || "Unnamed node"}</strong>
              <dl>
                <dt>Agent</dt>
                <dd>
                  {agent
                    ? agentLabel(agent)
                    : node.target_agent_id || "Not selected"}
                </dd>
                <dt>Objective</dt>
                <dd>{node.objective || "Not provided"}</dd>
                <dt>Input payload</dt>
                <dd>
                  <pre>{previewPayload(node.input_payload)}</pre>
                </dd>
                <dt>Expected output contract</dt>
                <dd>{node.expected_output_contract || "Not provided"}</dd>
              </dl>
            </li>
          );
        })}
      </ul>
      <h5>Edges</h5>
      <ul>
        {edges.map((edge) => (
          <li key={edge.id}>
            {edge.from || "?"} → {edge.to || "?"}
          </li>
        ))}
      </ul>
      <p>
        Workflow definitions influence orchestration only. They do not grant
        authority or bypass Friday’s approval and ToolGateway path.
      </p>
    </section>
  );
}
