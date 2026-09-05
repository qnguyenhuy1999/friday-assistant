import type {
  Agent,
  Failure,
  JsonValue,
  Skill,
  TaskSkillBinding,
  Workflow,
} from "@friday/contracts";
import { useEffect, useMemo, useRef, useState } from "react";
import { useAgent, useAgents } from "../hooks/use-agents";
import { useSkillDetails, useSkills } from "../hooks/use-skills";
import {
  useBindTaskWorkflow,
  useClearTaskAgent,
  useReplaceTaskSkills,
  usePutTaskAgent,
  useStartRun,
  useTask,
  useTaskAgentBinding,
  useTaskSkills,
  useTaskWorkflowBinding,
  useUnbindTaskWorkflow,
} from "../hooks/use-tasks";
import { useWorkflow, useWorkflows } from "../hooks/use-workflows";
import { calculateLaunchReadiness } from "./task-launch-readiness";

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

function agentReadinessWarning(agent: Agent): string | null {
  if (agent.status === "archived")
    return "This Agent is archived and cannot be reactivated through the supported lifecycle. Future unresolved Runs cannot resolve this binding. Clear or replace the Task binding before starting another Run.";
  const reason = agentBindingReason(agent);
  return reason
    ? `${reason} A future unresolved Run may fail Agent resolution while this Agent remains unavailable.`
    : null;
}

function workflowBindingReason(workflow: Workflow): string | null {
  return workflow.status === "archived"
    ? "Archived — cannot be newly bound."
    : null;
}

function workflowReadinessWarning(workflow: Workflow): string | null {
  if (workflow.status === "archived")
    return "This Workflow is archived and cannot be reactivated through the supported lifecycle. Future unresolved Runs cannot resolve this binding. Clear or replace the Task binding before starting another Run.";
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

const MAX_TASK_SKILLS = 16;

function sameSkillIds(left: string[], right: string[]): boolean {
  return (
    left.length === right.length &&
    left.every((skillId, index) => skillId === right[index])
  );
}

function orderedSkillIds(bindings: TaskSkillBinding[]): string[] {
  return [...bindings]
    .sort((left, right) => left.position - right.position)
    .map((binding) => binding.skill_id);
}

function skillBindingReason(skill: Skill): string | null {
  if (skill.status === "archived") return "Archived — cannot be newly bound.";
  if (skill.status === "disabled") return "Disabled — cannot be newly bound.";
  if (skill.active_revision_id === null)
    return "No selected revision — cannot be newly bound.";
  return null;
}

function skillReadinessWarning(skill: Skill): string | null {
  if (skill.status === "disabled")
    return `The bound Skill "${skill.display_name}" is disabled and is not runtime-resolvable. Remove or replace this Skill before starting another Run.`;
  if (skill.status === "archived")
    return `The bound Skill "${skill.display_name}" is archived and cannot resolve for future unresolved Runs. Remove or replace this Skill before starting another Run.`;
  if (skill.active_revision_id === null)
    return `The bound Skill "${skill.display_name}" has no selected revision and cannot be resolved. Remove or replace this Skill before starting another Run.`;
  return null;
}

function skillOptionLabel(skill: Skill, reason: string | null): string {
  return [
    skill.display_name,
    skill.key,
    skill.status,
    `selected revision: ${skill.active_revision_id ?? "none"}`,
    reason,
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
  const taskSkills = useTaskSkills(taskId);
  const agents = useAgents();
  const workflows = useWorkflows();
  const skills = useSkills();
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
  const replaceSkills = useReplaceTaskSkills(taskId);
  const startRun = useStartRun();
  const [selectedAgentId, setSelectedAgentId] = useState("");
  const [selectedWorkflowId, setSelectedWorkflowId] = useState("");
  const [selectedSkillId, setSelectedSkillId] = useState("");
  const [draftSkillIds, setDraftSkillIds] = useState<string[] | null>(null);
  const previousPersistedSkillIds = useRef<string[] | null>(null);

  const persistedSkillIds = useMemo(
    () => orderedSkillIds(taskSkills.data ?? []),
    [taskSkills.data],
  );
  const boundSkillDetails = useSkillDetails(persistedSkillIds);

  useEffect(() => {
    const previous = previousPersistedSkillIds.current;
    if (
      draftSkillIds === null ||
      previous === null ||
      sameSkillIds(draftSkillIds, previous)
    ) {
      setDraftSkillIds(persistedSkillIds);
    }
    previousPersistedSkillIds.current = persistedSkillIds;
  }, [draftSkillIds, persistedSkillIds]);

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
  const boundSkillDetailsById = new Map(
    boundSkillDetails.flatMap((query) =>
      query.data ? [[query.data.id, query.data] as const] : [],
    ),
  );
  const registrySkillItems =
    skills.data?.pages.flatMap((page) => page.items) ?? [];
  const registrySkillsById = new Map(
    registrySkillItems.map((skill) => [skill.id, skill] as const),
  );
  const persistedSkillIdSet = new Set(persistedSkillIds);
  const displayedSkillIds = draftSkillIds ?? persistedSkillIds;
  const hasUnsavedSkillDraft =
    draftSkillIds !== null && !sameSkillIds(draftSkillIds, persistedSkillIds);
  const skillBindingsLoading = taskSkills.isLoading || taskSkills.isFetching;
  const skillBindingLoadError = taskSkills.isError;
  const skillDetailsLoading = boundSkillDetails.some(
    (query) => query.isLoading || query.isFetching,
  );
  const skillDetailsLoadError =
    boundSkillDetails.some((query) => query.isError) ||
    (!skillDetailsLoading &&
      persistedSkillIds.some((skillId) => !boundSkillDetailsById.has(skillId)));
  const unresolvableBoundSkill = persistedSkillIds
    .map((skillId) => boundSkillDetailsById.get(skillId))
    .find(
      (skill): skill is Skill =>
        skill !== undefined && skillReadinessWarning(skill) !== null,
    );
  const unresolvableSkillReason = unresolvableBoundSkill
    ? skillReadinessWarning(unresolvableBoundSkill)
    : null;
  const boundSkillMetadata = (skillId: string): Skill | undefined =>
    persistedSkillIdSet.has(skillId)
      ? boundSkillDetailsById.get(skillId)
      : (registrySkillsById.get(skillId) ?? boundSkillDetailsById.get(skillId));
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
  const bindingsLoading =
    agentBinding.isLoading ||
    agentBinding.isFetching ||
    workflowBinding.isLoading ||
    workflowBinding.isFetching;
  const inconsistent = boundAgentId !== null && boundWorkflowId !== null;
  const targetDetailsLoading =
    (boundAgentId !== null &&
      (boundAgent.isLoading || boundAgent.isFetching)) ||
    (boundWorkflowId !== null &&
      (boundWorkflow.isLoading || boundWorkflow.isFetching));
  const targetDetailsError =
    (boundAgentId !== null && boundAgent.isError) ||
    (boundWorkflowId !== null && boundWorkflow.isError);
  const bindingsReady = !bindingsLoading && !bindingLoadError;
  const mutationPending =
    putAgent.isPending ||
    clearAgent.isPending ||
    bindWorkflow.isPending ||
    unbindWorkflow.isPending;
  const skillMutationPending = replaceSkills.isPending;
  const launchReadiness = calculateLaunchReadiness({
    taskStatus: currentTask.status,
    bindingsLoading,
    bindingLoadError,
    inconsistent,
    targetDetailsLoading,
    targetDetailsError,
    archivedAgent: currentAgent?.status === "archived",
    archivedWorkflow: currentWorkflow?.status === "archived",
    mutationPending,
    startRunPending: startRun.isPending,
    skillBindingsLoading,
    skillBindingLoadError,
    skillDetailsLoading,
    skillDetailsLoadError,
    unresolvableSkillReason,
    skillMutationPending,
    unsavedSkillDraft: hasUnsavedSkillDraft,
  });

  const skillEditingDisabled =
    skillBindingsLoading || skillBindingLoadError || skillMutationPending;

  function draftSkillIdsForUpdate(): string[] {
    return [...(draftSkillIds ?? persistedSkillIds)];
  }

  function addSelectedSkill() {
    const selected = registrySkillsById.get(selectedSkillId);
    const draft = draftSkillIdsForUpdate();
    const reason = selected
      ? draft.includes(selected.id)
        ? "Already in draft composition — duplicate unavailable."
        : draft.length >= MAX_TASK_SKILLS
          ? `Maximum of ${MAX_TASK_SKILLS} Skills per Task reached.`
          : skillBindingReason(selected)
      : null;
    if (!selected || reason !== null) return;
    setDraftSkillIds([...draft, selected.id]);
    setSelectedSkillId("");
  }

  function moveSkill(index: number, direction: -1 | 1) {
    const draft = draftSkillIdsForUpdate();
    const nextIndex = index + direction;
    if (nextIndex < 0 || nextIndex >= draft.length) return;
    const movedSkillId = draft[index];
    const displacedSkillId = draft[nextIndex];
    if (movedSkillId === undefined || displacedSkillId === undefined) return;
    draft[index] = displacedSkillId;
    draft[nextIndex] = movedSkillId;
    setDraftSkillIds(draft);
  }

  function removeSkill(skillId: string) {
    setDraftSkillIds(draftSkillIdsForUpdate().filter((id) => id !== skillId));
  }

  function clearAllSkills() {
    if (
      window.confirm(
        "Clear all Skills from this Task? Save the composition to persist this atomic replacement.",
      )
    ) {
      setDraftSkillIds([]);
      setSelectedSkillId("");
    }
  }

  function discardSkillChanges() {
    setDraftSkillIds([...persistedSkillIds]);
    setSelectedSkillId("");
  }

  function saveSkillComposition() {
    const draft = draftSkillIdsForUpdate();
    if (sameSkillIds(draft, persistedSkillIds)) return;
    replaceSkills.mutate(draft, {
      onSuccess: (bindings) => setDraftSkillIds(orderedSkillIds(bindings)),
    });
  }

  function bindSelectedAgent() {
    if (selectedAgent && agentBindingReason(selectedAgent) === null)
      putAgent.mutate(selectedAgent.id);
  }

  function bindSelectedWorkflow() {
    if (selectedWorkflow && workflowBindingReason(selectedWorkflow) === null)
      bindWorkflow.mutate(selectedWorkflow.id);
  }

  function start() {
    if (!launchReadiness.canStartRun) return;
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

      <section aria-labelledby="task-skill-composition-title">
        <h3 id="task-skill-composition-title">Skill composition</h3>
        <p>
          Skills influence reasoning only. They never grant filesystem,
          shell/process, MCP, browser/computer, messaging, provider, tool,
          approval, scheduling, or execution authority.
        </p>
        <p>
          Task Skill composition is mutable future configuration. Changes can
          affect unresolved Runs, including queued Runs whose Skill resolution
          has not yet been frozen. Once Friday freezes Run Skill resolution,
          later Task Skill or selected revision changes do not rewrite that Run.
        </p>
        {skillBindingsLoading && <p>Loading persisted Skill bindings…</p>}
        {skillBindingLoadError && (
          <p role="alert">
            Failed to load persisted Skill bindings. Skill composition controls
            are unavailable until the Task bindings can be verified.
          </p>
        )}
        {!skillBindingsLoading && !skillBindingLoadError && (
          <>
            {hasUnsavedSkillDraft && (
              <p role="status">
                Skill composition has unsaved changes. Save or discard changes
                before starting a Run.
              </p>
            )}
            {skillDetailsLoading && <p>Verifying bound Skill details…</p>}
            {skillDetailsLoadError && (
              <p role="alert">
                Failed to verify bound Skill details. The persisted Skill
                composition cannot be verified right now; Run start is
                unavailable.
              </p>
            )}
            <ol aria-label="Task Skill composition">
              {displayedSkillIds.map((skillId, index) => {
                const metadata = boundSkillMetadata(skillId);
                const displayName =
                  metadata?.display_name ?? `Skill ${skillId}`;
                const readinessWarning = metadata
                  ? skillReadinessWarning(metadata)
                  : null;
                return (
                  <li key={skillId}>
                    <article aria-label={`Skill ${index + 1}: ${displayName}`}>
                      <h4>
                        {index + 1}. {displayName}
                      </h4>
                      <dl>
                        <dt>Skill ID</dt>
                        <dd>{skillId}</dd>
                        <dt>Key</dt>
                        <dd>{valueOrUnavailable(metadata?.key)}</dd>
                        <dt>Status</dt>
                        <dd>{valueOrUnavailable(metadata?.status)}</dd>
                        <dt>Selected revision ID</dt>
                        <dd>
                          {valueOrUnavailable(metadata?.active_revision_id)}
                        </dd>
                      </dl>
                      {!metadata && persistedSkillIdSet.has(skillId) && (
                        <p role="alert">
                          Current Skill metadata is unavailable. Remove or
                          replace this binding, then save the repaired
                          composition.
                        </p>
                      )}
                      {readinessWarning && (
                        <p role="alert">{readinessWarning}</p>
                      )}
                      <button
                        type="button"
                        aria-label={`Move ${displayName} up`}
                        disabled={skillEditingDisabled || index === 0}
                        onClick={() => moveSkill(index, -1)}
                      >
                        Move up
                      </button>
                      <button
                        type="button"
                        aria-label={`Move ${displayName} down`}
                        disabled={
                          skillEditingDisabled ||
                          index === displayedSkillIds.length - 1
                        }
                        onClick={() => moveSkill(index, 1)}
                      >
                        Move down
                      </button>
                      <button
                        type="button"
                        aria-label={`Remove ${displayName}`}
                        disabled={skillEditingDisabled}
                        onClick={() => removeSkill(skillId)}
                      >
                        Remove
                      </button>
                    </article>
                  </li>
                );
              })}
            </ol>
            {displayedSkillIds.length === 0 && <p>No Skills are configured.</p>}

            <h4>Add Skill</h4>
            {skills.isLoading && <p>Loading Skills…</p>}
            {skills.isError && (
              <p role="alert">
                Failed to load Skills. Load the Skill registry before adding a
                new binding.
              </p>
            )}
            <label htmlFor="task-skill-add">Skill</label>
            <select
              id="task-skill-add"
              value={selectedSkillId}
              disabled={
                skillEditingDisabled ||
                skills.isError ||
                skills.isLoading ||
                displayedSkillIds.length >= MAX_TASK_SKILLS
              }
              onChange={(event) => setSelectedSkillId(event.target.value)}
            >
              <option value="">Select a Skill</option>
              {registrySkillItems.map((skill) => {
                const reason = displayedSkillIds.includes(skill.id)
                  ? "Already in draft composition — duplicate unavailable."
                  : displayedSkillIds.length >= MAX_TASK_SKILLS
                    ? `Maximum of ${MAX_TASK_SKILLS} Skills per Task reached.`
                    : skillBindingReason(skill);
                return (
                  <option
                    key={skill.id}
                    value={skill.id}
                    disabled={reason !== null}
                  >
                    {skillOptionLabel(skill, reason)}
                  </option>
                );
              })}
            </select>
            <button
              type="button"
              disabled={
                skillEditingDisabled ||
                skills.isError ||
                skills.isLoading ||
                selectedSkillId === "" ||
                displayedSkillIds.length >= MAX_TASK_SKILLS ||
                (() => {
                  const selected = registrySkillsById.get(selectedSkillId);
                  return (
                    selected === undefined ||
                    skillBindingReason(selected) !== null
                  );
                })()
              }
              onClick={addSelectedSkill}
            >
              Add selected Skill
            </button>
            {skills.hasNextPage && (
              <button
                type="button"
                disabled={skills.isFetchingNextPage}
                onClick={() => void skills.fetchNextPage()}
              >
                {skills.isFetchingNextPage
                  ? "Loading more Skills…"
                  : "Load more Skills"}
              </button>
            )}
            <p>Maximum Skills per Task: {MAX_TASK_SKILLS}.</p>
            <button
              type="button"
              disabled={skillEditingDisabled || displayedSkillIds.length === 0}
              onClick={clearAllSkills}
            >
              Clear all Skills
            </button>
            <button
              type="button"
              disabled={skillEditingDisabled || !hasUnsavedSkillDraft}
              onClick={saveSkillComposition}
            >
              {replaceSkills.isPending
                ? "Saving Skill composition…"
                : "Save Skill composition"}
            </button>
            <button
              type="button"
              disabled={skillEditingDisabled || !hasUnsavedSkillDraft}
              onClick={discardSkillChanges}
            >
              Discard changes
            </button>
            {replaceSkills.isError && (
              <p role="alert">
                Failed to save Skill composition. The server rejected the atomic
                replacement; the local draft was kept and no partial Skill
                changes were applied.
              </p>
            )}
          </>
        )}
      </section>

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
          {currentAgent && agentReadinessWarning(currentAgent) !== null && (
            <p role="alert">
              Launch-readiness warning: {agentReadinessWarning(currentAgent)}
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

      {bindingsReady && (
        <>
          <h3>Execution preview</h3>
          {inconsistent && (
            <p>
              Execution target: <strong>Unavailable</strong> — both Agent and
              Workflow bindings are present.
            </p>
          )}
          {!inconsistent &&
            boundAgentId === null &&
            boundWorkflowId === null && (
              <p>
                Execution target: <strong>Default Friday runtime</strong>
              </p>
            )}
          {!inconsistent && boundAgentId !== null && (
            <p>
              Execution target: <strong>Agent</strong> —{" "}
              {valueOrUnavailable(currentAgent?.display_name)}
              <br />
              Selected revision:{" "}
              {valueOrUnavailable(currentAgent?.active_revision_id)}
            </p>
          )}
          {!inconsistent && boundWorkflowId !== null && (
            <p>
              Execution target: <strong>Workflow</strong> —{" "}
              {valueOrUnavailable(currentWorkflow?.display_name)}
              <br />
              Selected revision:{" "}
              {valueOrUnavailable(currentWorkflow?.active_revision_id)}
            </p>
          )}
          <h4>Skill composition</h4>
          {persistedSkillIds.length === 0 ? (
            <p>Skill composition: None</p>
          ) : (
            <ol aria-label="Persisted Skill execution preview">
              {persistedSkillIds.map((skillId, index) => {
                const metadata = boundSkillMetadata(skillId);
                return (
                  <li key={skillId}>
                    {index + 1}. {metadata?.display_name ?? `Skill ${skillId}`}{" "}
                    — selected revision{" "}
                    {valueOrUnavailable(metadata?.active_revision_id)}
                  </li>
                );
              })}
            </ol>
          )}
          <p>
            Execution preview shows the currently persisted Skill values, not
            frozen Run provenance. Friday freezes the Skill revisions later
            during worker resolution; until then, Task Skill bindings or
            selected revisions may affect an unresolved Run.
          </p>
          <p>
            Starting a Run queues it for Friday&apos;s worker. The worker owns
            Agent or Workflow resolution; the browser never chooses an execution
            path.
          </p>
          {!launchReadiness.canStartRun && (
            <p role="alert">
              <strong>Launch readiness: unavailable</strong>
              <br />
              {launchReadiness.unavailableReason}
            </p>
          )}
          <button
            type="button"
            disabled={!launchReadiness.canStartRun}
            onClick={start}
          >
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
