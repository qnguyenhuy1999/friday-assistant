import type { Skill, SkillRevision } from "@friday/contracts";
import { useState, type FormEvent } from "react";
import { SkillUsageEvidenceSection } from "../components/skill-usage-evidence-section";
import {
  useActivateSkillRevision,
  useCreateSkillRevision,
  useSkill,
  useSkillRevision,
  useSkillLifecycle,
  useSkillRevisions,
} from "../hooks/use-skills";

const MAX_INSTRUCTIONS_LENGTH = 32_000;

function formatTime(value: string) {
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.valueOf()) ? value : timestamp.toLocaleString();
}

function uniqueRevisions(pages: SkillRevision[][]): SkillRevision[] {
  const seenVersions = new Set<number>();
  return pages.flat().filter((revision) => {
    if (seenVersions.has(revision.version)) return false;
    seenVersions.add(revision.version);
    return true;
  });
}

function selectedRevisionLabel(skill: Skill, revision: SkillRevision) {
  if (skill.status === "active") return `v${revision.version} - active`;
  return `v${revision.version} - selected, Skill ${skill.status}`;
}

function RevisionInspection({
  revision,
  skill,
  selectedRevision,
  activate,
  activationPending,
}: {
  revision: SkillRevision;
  skill: Skill;
  selectedRevision: SkillRevision | undefined;
  activate: (revision: SkillRevision) => void;
  activationPending: boolean;
}) {
  const isSelectedRevision = revision.id === skill.active_revision_id;
  const isNewerRevision =
    skill.active_revision_id === null ||
    (selectedRevision !== undefined &&
      revision.version > selectedRevision.version);
  const isHistoricalRevision =
    selectedRevision !== undefined &&
    revision.version <= selectedRevision.version;
  const canActivate =
    !isSelectedRevision &&
    skill.status !== "archived" &&
    revision.source_kind !== "generated" &&
    isNewerRevision;

  return (
    <li>
      <article aria-label={`Skill revision v${revision.version}`}>
        <h4>
          {isSelectedRevision
            ? selectedRevisionLabel(skill, revision)
            : `v${revision.version}`}
        </h4>
        <dl>
          <dt>Revision ID</dt>
          <dd>{revision.id}</dd>
          <dt>Source kind</dt>
          <dd>{revision.source_kind}</dd>
          <dt>Content SHA-256</dt>
          <dd>{revision.content_sha256}</dd>
          <dt>Created</dt>
          <dd>{formatTime(revision.created_at)}</dd>
          <dt>Immutable instructions</dt>
          <dd>
            <pre style={{ whiteSpace: "pre-wrap" }}>
              {revision.instructions}
            </pre>
          </dd>
        </dl>
        {revision.source_kind === "generated" && (
          <p>Generated - promotion controlled.</p>
        )}
        {!isSelectedRevision && isHistoricalRevision && (
          <p>Historical revision - rollback required.</p>
        )}
        {canActivate && (
          <>
            {skill.status === "disabled" && (
              <p>
                Activating this revision changes the selected revision pointer.
                It does not re-enable this disabled Skill.
              </p>
            )}
            <button
              type="button"
              disabled={activationPending}
              onClick={() => activate(revision)}
            >
              Activate v{revision.version}
            </button>
          </>
        )}
      </article>
    </li>
  );
}

export function SkillDetailPage({
  skillId,
  onBack,
  onViewRun,
}: {
  skillId: string;
  onBack: () => void;
  onViewRun?: (runId: string) => void;
}) {
  const skill = useSkill(skillId);
  const selectedRevisionLookup = useSkillRevision(
    skillId,
    skill.data?.active_revision_id ?? null,
  );
  const revisions = useSkillRevisions(skillId);
  const createRevision = useCreateSkillRevision(skillId);
  const activateRevision = useActivateSkillRevision(skillId);
  const lifecycle = useSkillLifecycle(skillId);
  const [instructions, setInstructions] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const [createdRevision, setCreatedRevision] = useState<number | null>(null);

  function submitRevision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!instructions.trim()) {
      setValidationError("Instructions are required.");
      return;
    }
    if (instructions.length > MAX_INSTRUCTIONS_LENGTH) {
      setValidationError("Instructions exceed the supported length.");
      return;
    }
    setValidationError(null);
    createRevision.mutate(
      { instructions, source_kind: "operator" },
      {
        onSuccess: (revision) => {
          setCreatedRevision(revision.version);
          setInstructions("");
        },
      },
    );
  }

  function activate(revision: SkillRevision) {
    if (
      window.confirm(
        `Activate Skill revision v${revision.version} for future Runs? Existing Runs keep their frozen revision.`,
      )
    ) {
      activateRevision.mutate(revision.id);
    }
  }

  function changeLifecycle(action: "disable" | "archive") {
    const label = action === "disable" ? "disable" : "archive";
    if (window.confirm(`Are you sure you want to ${label} this Skill?`)) {
      lifecycle.mutate(action);
    }
  }

  if (skill.isLoading) return <p>Loading Skill...</p>;
  if (skill.isError || !skill.data)
    return <p role="alert">Failed to load Skill.</p>;

  const current = skill.data;
  const revisionItems = uniqueRevisions(revisions.data?.pages ?? []);
  const selectedRevisionResponse = selectedRevisionLookup.data;
  const selectedRevision =
    !selectedRevisionLookup.isError &&
    selectedRevisionResponse?.id === current.active_revision_id &&
    selectedRevisionResponse.skill_id === current.id
      ? selectedRevisionResponse
      : undefined;
  const selectedRevisionVerificationPending =
    current.active_revision_id !== null && selectedRevisionLookup.isPending;
  const selectedRevisionVerificationFailed =
    current.active_revision_id !== null &&
    !selectedRevisionVerificationPending &&
    (selectedRevisionLookup.isError ||
      (selectedRevisionLookup.isSuccess && selectedRevision === undefined));

  return (
    <section>
      <button type="button" onClick={onBack}>
        Back to Skills
      </button>
      <h2>{current.display_name}</h2>
      <dl>
        <dt>Skill ID</dt>
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
              Disable Skill
            </button>
          )}{" "}
          <button
            type="button"
            disabled={lifecycle.isPending}
            onClick={() => changeLifecycle("archive")}
          >
            Archive Skill
          </button>
        </p>
      )}
      {lifecycle.isError && (
        <p role="alert">Failed to update Skill lifecycle.</p>
      )}
      {selectedRevisionVerificationPending && (
        <p role="status">Verifying selected Skill revision...</p>
      )}
      {selectedRevisionVerificationFailed && (
        <p role="alert">
          Failed to verify the selected Skill revision. Revision activation is
          unavailable.
        </p>
      )}

      <h3>Immutable revision history</h3>
      <p>
        Revision content is immutable. A selected revision pointer and a
        runtime-resolvable Skill are separate states.
      </p>
      {revisions.isLoading && <p>Loading Skill revisions...</p>}
      {revisions.isError && <p role="alert">Failed to load Skill revisions.</p>}
      {!revisions.isLoading &&
        !revisions.isError &&
        revisionItems.length === 0 && <p>No revisions yet.</p>}
      <ol aria-label="Skill revision history">
        {revisionItems.map((revision) => (
          <RevisionInspection
            key={revision.id}
            revision={revision}
            skill={current}
            selectedRevision={selectedRevision}
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
            ? "Loading older revisions..."
            : "Load older revisions"}
        </button>
      )}
      {activateRevision.isError && (
        <p role="alert">Failed to activate Skill revision.</p>
      )}
      <p>
        Activation changes the selected pointer for future Run resolution only.
        It does not rewrite immutable revisions or frozen existing Runs.
      </p>

      <SkillUsageEvidenceSection
        skillId={current.id}
        onViewRun={onViewRun ?? (() => undefined)}
      />

      {current.status === "archived" ? (
        <p>
          This Skill is archived and read-only. Its metadata, immutable
          revisions, and provenance remain available for inspection.
        </p>
      ) : (
        <>
          <h3>Create immutable revision</h3>
          <form onSubmit={submitRevision} aria-label="Create Skill revision">
            <label htmlFor="skill-revision-instructions">Instructions</label>
            <textarea
              id="skill-revision-instructions"
              value={instructions}
              maxLength={MAX_INSTRUCTIONS_LENGTH}
              onChange={(event) => setInstructions(event.target.value)}
              required
            />
            <p>
              Operator authoring creates an immutable operator revision. The new
              revision is not selected automatically.
            </p>
            <button type="submit" disabled={createRevision.isPending}>
              Create immutable revision
            </button>
          </form>
          {validationError && <p role="alert">{validationError}</p>}
          {createRevision.isError && (
            <p role="alert">Failed to create Skill revision.</p>
          )}
          {createdRevision !== null && (
            <p role="status">
              Created revision v{createdRevision}. It is not selected until
              activated.
            </p>
          )}
        </>
      )}
    </section>
  );
}
