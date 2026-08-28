import { useState, type FormEvent } from "react";
import { useAgents, useCreateAgent } from "../hooks/use-agents";

const MAX_KEY_LENGTH = 96;
const MAX_DISPLAY_NAME_LENGTH = 256;
const MAX_DESCRIPTION_LENGTH = 4000;

export function AgentsPage({
  onViewAgent,
}: {
  onViewAgent: (id: string) => void;
}) {
  const agents = useAgents();
  const create = useCreateAgent();
  const [key, setKey] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedKey = key.trim();
    const normalizedName = displayName.trim();
    if (!normalizedKey || !normalizedName) {
      setValidationError("Key and display name are required.");
      return;
    }
    if (
      normalizedKey.length > MAX_KEY_LENGTH ||
      normalizedName.length > MAX_DISPLAY_NAME_LENGTH ||
      description.length > MAX_DESCRIPTION_LENGTH
    ) {
      setValidationError("One or more fields exceed the supported length.");
      return;
    }
    setValidationError(null);
    create.mutate(
      {
        key: normalizedKey,
        display_name: normalizedName,
        description: description.trim(),
      },
      { onSuccess: (agent) => onViewAgent(agent.id) },
    );
  }

  const items = agents.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <section>
      <h2>Agents</h2>
      <p>
        Agents are versioned reasoning definitions; they do not grant authority.
      </p>
      <form onSubmit={submit} aria-label="Create agent">
        <label htmlFor="agent-key">Key</label>
        <input
          id="agent-key"
          value={key}
          maxLength={MAX_KEY_LENGTH}
          onChange={(event) => setKey(event.target.value)}
          required
        />
        <label htmlFor="agent-display-name">Display name</label>
        <input
          id="agent-display-name"
          value={displayName}
          maxLength={MAX_DISPLAY_NAME_LENGTH}
          onChange={(event) => setDisplayName(event.target.value)}
          required
        />
        <label htmlFor="agent-description">Description</label>
        <textarea
          id="agent-description"
          value={description}
          maxLength={MAX_DESCRIPTION_LENGTH}
          onChange={(event) => setDescription(event.target.value)}
        />
        <button type="submit" disabled={create.isPending}>
          Create agent
        </button>
      </form>
      {validationError && <p role="alert">{validationError}</p>}
      {create.isError && <p role="alert">Failed to create agent.</p>}
      {agents.isLoading && <p>Loading agents…</p>}
      {agents.isError && <p role="alert">Failed to load agents.</p>}
      {!agents.isLoading && !agents.isError && items.length === 0 && (
        <p>
          No Agents yet. Create one to begin its immutable revision history.
        </p>
      )}
      <ul aria-label="Agent registry">
        {items.map((agent) => (
          <li key={agent.id}>
            <button type="button" onClick={() => onViewAgent(agent.id)}>
              {agent.display_name}
            </button>{" "}
            — key: {agent.key} — status: {agent.status} — selected revision:{" "}
            {agent.active_revision_id ?? "none"}
          </li>
        ))}
      </ul>
      {agents.hasNextPage && (
        <button
          type="button"
          disabled={agents.isFetchingNextPage}
          onClick={() => void agents.fetchNextPage()}
        >
          {agents.isFetchingNextPage ? "Loading more…" : "Load more Agents"}
        </button>
      )}
    </section>
  );
}
