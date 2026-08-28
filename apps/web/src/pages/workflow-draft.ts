import type {
  JsonValue,
  WorkflowEdgeInput,
  WorkflowNodeInput,
} from "@friday/contracts";

export const MAX_WORKFLOW_NODES = 64;
export const MAX_WORKFLOW_EDGES = 256;
export const MAX_WORKFLOW_KEY_LENGTH = 128;
export const MAX_WORKFLOW_DISPLAY_NAME_LENGTH = 256;
export const MAX_WORKFLOW_DESCRIPTION_LENGTH = 4000;
export const MAX_WORKFLOW_NODE_OBJECTIVE_LENGTH = 4000;
export const MAX_WORKFLOW_OUTPUT_CONTRACT_LENGTH = 4000;
export const MAX_WORKFLOW_INPUT_LENGTH = 16_384;

const MACHINE_KEY = /^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$/;

export function isValidMachineKey(value: string): boolean {
  return MACHINE_KEY.test(value);
}

export function parseWorkflowInputPayload(value: string): {
  value?: JsonValue;
  error?: string;
} {
  try {
    return { value: JSON.parse(value) as JsonValue };
  } catch {
    return { error: "Input payload must be valid JSON." };
  }
}

/** Small operator-feedback validator. The API/domain remains authoritative. */
export function validateWorkflowDraft(
  nodes: readonly WorkflowNodeInput[],
  edges: readonly WorkflowEdgeInput[],
): string | null {
  if (nodes.length === 0)
    return "Add at least one node before creating a revision.";
  if (nodes.length > MAX_WORKFLOW_NODES)
    return `A revision cannot contain more than ${MAX_WORKFLOW_NODES} nodes.`;
  if (edges.length > MAX_WORKFLOW_EDGES)
    return `A revision cannot contain more than ${MAX_WORKFLOW_EDGES} edges.`;

  const keys = nodes.map((node) => node.node_key);
  if (new Set(keys).size !== keys.length) return "Node keys must be unique.";
  if (nodes.some((node) => !isValidMachineKey(node.node_key)))
    return "Node keys must start with a lowercase letter and use machine-readable characters.";
  if (nodes.some((node) => !node.target_agent_id))
    return "Every node must target an Agent.";
  if (nodes.some((node) => !node.objective.trim()))
    return "Every node needs an objective.";
  if (nodes.some((node) => !node.expected_output_contract.trim()))
    return "Every node needs an expected output contract.";

  const knownKeys = new Set(keys);
  const pairs = new Set<string>();
  const indegree = new Map(keys.map((key) => [key, 0]));
  const outgoing = new Map(keys.map((key) => [key, [] as string[]]));
  for (const edge of edges) {
    if (!knownKeys.has(edge.from) || !knownKeys.has(edge.to))
      return "Edges must reference existing node keys.";
    if (edge.from === edge.to) return "An edge cannot point to the same node.";
    const pair = `${edge.from}\u0000${edge.to}`;
    if (pairs.has(pair)) return "Duplicate edges are not allowed.";
    pairs.add(pair);
    indegree.set(edge.to, (indegree.get(edge.to) ?? 0) + 1);
    outgoing.get(edge.from)?.push(edge.to);
  }

  const queue = keys.filter((key) => indegree.get(key) === 0).sort();
  let visited = 0;
  while (queue.length > 0) {
    const key = queue.shift();
    if (!key) continue;
    visited += 1;
    for (const target of [...(outgoing.get(key) ?? [])].sort()) {
      const next = (indegree.get(target) ?? 0) - 1;
      indegree.set(target, next);
      if (next === 0) {
        queue.push(target);
        queue.sort();
      }
    }
  }
  return visited === nodes.length
    ? null
    : "The workflow graph cannot contain cycles.";
}
