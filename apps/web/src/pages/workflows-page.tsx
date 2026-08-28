import { useState, type FormEvent } from "react";
import { useCreateWorkflow, useWorkflows } from "../hooks/use-workflows";
import {
  isValidMachineKey,
  MAX_WORKFLOW_DESCRIPTION_LENGTH,
  MAX_WORKFLOW_DISPLAY_NAME_LENGTH,
  MAX_WORKFLOW_KEY_LENGTH,
} from "./workflow-draft";

export function WorkflowsPage({
  onViewWorkflow,
}: {
  onViewWorkflow: (id: string) => void;
}) {
  const workflows = useWorkflows();
  const create = useCreateWorkflow();
  const [key, setKey] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedKey = key.trim();
    const normalizedName = displayName.trim();
    const normalizedDescription = description.trim();
    if (!normalizedKey || !normalizedName) {
      setValidationError("Key and display name are required.");
      return;
    }
    if (!isValidMachineKey(normalizedKey)) {
      setValidationError(
        "Key must start with a lowercase letter and use machine-readable characters.",
      );
      return;
    }
    if (
      normalizedKey.length > MAX_WORKFLOW_KEY_LENGTH ||
      normalizedName.length > MAX_WORKFLOW_DISPLAY_NAME_LENGTH ||
      normalizedDescription.length > MAX_WORKFLOW_DESCRIPTION_LENGTH
    ) {
      setValidationError("One or more fields exceed the supported length.");
      return;
    }
    setValidationError(null);
    create.mutate(
      {
        key: normalizedKey,
        display_name: normalizedName,
        description: normalizedDescription,
      },
      { onSuccess: (workflow) => onViewWorkflow(workflow.id) },
    );
  }

  const items = workflows.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <section>
      <h2>Workflows</h2>
      <p>
        Workflows define orchestration structure. They never grant tool,
        filesystem, approval, or provider authority.
      </p>
      <form onSubmit={submit} aria-label="Create workflow">
        <label htmlFor="workflow-key">Key</label>
        <input
          id="workflow-key"
          value={key}
          maxLength={MAX_WORKFLOW_KEY_LENGTH}
          onChange={(event) => setKey(event.target.value)}
          required
        />
        <label htmlFor="workflow-display-name">Display name</label>
        <input
          id="workflow-display-name"
          value={displayName}
          maxLength={MAX_WORKFLOW_DISPLAY_NAME_LENGTH}
          onChange={(event) => setDisplayName(event.target.value)}
          required
        />
        <label htmlFor="workflow-description">Description</label>
        <textarea
          id="workflow-description"
          value={description}
          maxLength={MAX_WORKFLOW_DESCRIPTION_LENGTH}
          onChange={(event) => setDescription(event.target.value)}
        />
        <button type="submit" disabled={create.isPending}>
          Create workflow
        </button>
      </form>
      {validationError && <p role="alert">{validationError}</p>}
      {create.isError && <p role="alert">Failed to create Workflow.</p>}
      {workflows.isLoading && <p>Loading Workflows…</p>}
      {workflows.isError && <p role="alert">Failed to load Workflows.</p>}
      {!workflows.isLoading && !workflows.isError && items.length === 0 && (
        <p>
          No Workflows yet. Create one to begin its immutable revision history.
        </p>
      )}
      <ul aria-label="Workflow registry">
        {items.map((workflow) => (
          <li key={workflow.id}>
            <button type="button" onClick={() => onViewWorkflow(workflow.id)}>
              {workflow.display_name}
            </button>{" "}
            — key: {workflow.key} — status: {workflow.status} — selected
            revision: {workflow.active_revision_id ?? "none"}
          </li>
        ))}
      </ul>
      {workflows.hasNextPage && (
        <button
          type="button"
          disabled={workflows.isFetchingNextPage}
          onClick={() => void workflows.fetchNextPage()}
        >
          {workflows.isFetchingNextPage ? "Loading more…" : "Load more"}
        </button>
      )}
    </section>
  );
}
