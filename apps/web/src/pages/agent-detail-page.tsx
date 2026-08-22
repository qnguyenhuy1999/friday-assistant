import type { JsonValue } from "@friday/contracts";
import { useState, type FormEvent } from "react";
import {
  useActivateAgentRevision,
  useAgent,
  useAgentLifecycle,
  useAgentRevisions,
  useCreateAgentRevision,
} from "../hooks/use-agents";

const MAX_INSTRUCTIONS_LENGTH = 32_000;
const MAX_RUNTIME_KIND_LENGTH = 64;
const DISALLOWED_CONFIG_KEY =
  /(?:credential|password|secret|token|api[_-]?key)/i;

function containsCredentialKey(value: JsonValue): boolean {
  if (Array.isArray(value)) return value.some(containsCredentialKey);
  if (value && typeof value === "object")
    return Object.entries(value).some(
      ([key, child]) =>
        DISALLOWED_CONFIG_KEY.test(key) || containsCredentialKey(child),
    );
  return false;
}

function formatTime(value: string) {
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.valueOf()) ? value : timestamp.toLocaleString();
}

export function AgentDetailPage({
  agentId,
  onBack,
}: {
  agentId: string;
  onBack: () => void;
}) {
  const agent = useAgent(agentId);
  const revisions = useAgentRevisions(agentId);
  const createRevision = useCreateAgentRevision(agentId);
  const activateRevision = useActivateAgentRevision(agentId);
  const lifecycle = useAgentLifecycle(agentId);
  const [instructions, setInstructions] = useState("");
  const [runtimeKind, setRuntimeKind] = useState("claude_cli");
  const [runtimeConfig, setRuntimeConfig] = useState("{}");
  const [sourceKind, setSourceKind] = useState<"operator" | "imported">(
    "operator",
  );
  const [validationError, setValidationError] = useState<string | null>(null);
  const [createdRevision, setCreatedRevision] = useState<number | null>(null);

  function submitRevision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedInstructions = instructions.trim();
    const normalizedKind = runtimeKind.trim();
    if (!normalizedInstructions || !normalizedKind) {
      setValidationError("Instructions and runtime kind are required.");
      return;
    }
    if (
      normalizedInstructions.length > MAX_INSTRUCTIONS_LENGTH ||
      normalizedKind.length > MAX_RUNTIME_KIND_LENGTH
    ) {
      setValidationError(
        "Instructions or runtime kind exceed the supported length.",
      );
      return;
    }
    let config: JsonValue;
    try {
      config = JSON.parse(runtimeConfig) as JsonValue;
    } catch {
      setValidationError("Runtime configuration must be valid JSON.");
      return;
    }
    if (!config || Array.isArray(config) || typeof config !== "object") {
      setValidationError("Runtime configuration must be a JSON object.");
      return;
    }
    if (containsCredentialKey(config)) {
      setValidationError(
        "Runtime configuration must not contain credentials or secrets.",
      );
      return;
    }
    setValidationError(null);
    createRevision.mutate(
      {
        instructions: normalizedInstructions,
        runtime_kind: normalizedKind,
        runtime_config: config as Record<string, JsonValue>,
        source_kind: sourceKind,
      },
      {
        onSuccess: (revision) => {
          setCreatedRevision(revision.version);
          setInstructions("");
        },
      },
    );
  }

  function activate(revisionId: string, version: number) {
    if (
      window.confirm(
        `Activate revision v${version} for future Runs? Existing Runs keep their frozen revision.`,
      )
    )
      activateRevision.mutate(revisionId);
  }

  function changeLifecycle(action: "disable" | "archive") {
    const label = action === "disable" ? "disable" : "archive";
    if (window.confirm(`Are you sure you want to ${label} this Agent?`))
      lifecycle.mutate(action);
  }

  if (agent.isLoading) return <p>Loading agent…</p>;
  if (agent.isError || !agent.data)
    return <p role="alert">Failed to load agent.</p>;
  const current = agent.data;
  return (
    <section>
      <button type="button" onClick={onBack}>
        Back to Agents
      </button>
      <h2>{current.display_name}</h2>
      <dl>
        <dt>Agent ID</dt>
        <dd>{current.id}</dd>
        <dt>Key</dt>
        <dd>{current.key}</dd>
        <dt>Description</dt>
        <dd>{current.description || "No description"}</dd>
        <dt>Lifecycle status</dt>
        <dd>{current.status}</dd>
        <dt>Active revision</dt>
        <dd>{current.active_revision_id ?? "No active revision"}</dd>
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
              Disable Agent
            </button>
          )}
          <button
            type="button"
            disabled={lifecycle.isPending}
            onClick={() => changeLifecycle("archive")}
          >
            Archive Agent
          </button>
        </p>
      )}
      {lifecycle.isError && (
        <p role="alert">Failed to update Agent lifecycle.</p>
      )}
      <h3>Immutable revision history</h3>
      <p>
        Revisions cannot be edited in place. Exactly one revision may be active.
      </p>
      {revisions.isLoading && <p>Loading revisions…</p>}
      {revisions.isError && <p role="alert">Failed to load revisions.</p>}
      {revisions.data?.length === 0 && <p>No revisions yet.</p>}
      <ol>
        {revisions.data?.map((revision) => {
          const active = revision.id === current.active_revision_id;
          return (
            <li key={revision.id}>
              <strong>
                v{revision.version}
                {active ? " — active" : ""}
              </strong>
              <dl>
                <dt>Revision ID</dt>
                <dd>{revision.id}</dd>
                <dt>Content SHA-256</dt>
                <dd>{revision.content_sha256}</dd>
                <dt>Runtime</dt>
                <dd>{revision.runtime_kind}</dd>
                <dt>Source</dt>
                <dd>{revision.source_kind}</dd>
                <dt>Created</dt>
                <dd>{formatTime(revision.created_at)}</dd>
                <dt>Instructions</dt>
                <dd>{revision.instructions}</dd>
              </dl>
              {!active && current.status !== "archived" && (
                <button
                  type="button"
                  disabled={activateRevision.isPending}
                  onClick={() => activate(revision.id, revision.version)}
                >
                  Activate v{revision.version}
                </button>
              )}
            </li>
          );
        })}
      </ol>
      {activateRevision.isError && (
        <p role="alert">Failed to activate revision.</p>
      )}
      <p>
        Activation affects future Run resolution only; it does not rewrite
        revisions or frozen existing Runs.
      </p>
      <h3>Create immutable revision</h3>
      <form onSubmit={submitRevision} aria-label="Create agent revision">
        <label htmlFor="revision-instructions">Instructions</label>
        <textarea
          id="revision-instructions"
          value={instructions}
          maxLength={MAX_INSTRUCTIONS_LENGTH}
          onChange={(event) => setInstructions(event.target.value)}
          required
        />
        <label htmlFor="revision-runtime-kind">Runtime kind</label>
        <input
          id="revision-runtime-kind"
          value={runtimeKind}
          maxLength={MAX_RUNTIME_KIND_LENGTH}
          onChange={(event) => setRuntimeKind(event.target.value)}
          required
        />
        <label htmlFor="revision-runtime-config">
          Runtime configuration (JSON object)
        </label>
        <textarea
          id="revision-runtime-config"
          value={runtimeConfig}
          onChange={(event) => setRuntimeConfig(event.target.value)}
        />
        <p>
          Runtime configuration is not authority. Credentials and secrets are
          not accepted here.
        </p>
        <label htmlFor="revision-source-kind">Source</label>
        <select
          id="revision-source-kind"
          value={sourceKind}
          onChange={(event) =>
            setSourceKind(event.target.value as "operator" | "imported")
          }
        >
          <option value="operator">Operator</option>
          <option value="imported">Imported</option>
        </select>
        <button type="submit" disabled={createRevision.isPending}>
          Create immutable revision
        </button>
      </form>
      {validationError && <p role="alert">{validationError}</p>}
      {createRevision.isError && <p role="alert">Failed to create revision.</p>}
      {createdRevision !== null && (
        <p role="status">
          Created revision v{createdRevision}. It is not active until activated.
        </p>
      )}
    </section>
  );
}
