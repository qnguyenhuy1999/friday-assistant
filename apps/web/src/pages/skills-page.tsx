import { useState, type FormEvent } from "react";
import { useCreateSkill, useSkills } from "../hooks/use-skills";

const MAX_KEY_LENGTH = 96;
const MAX_DISPLAY_NAME_LENGTH = 256;
const MAX_DESCRIPTION_LENGTH = 4000;
const SKILL_KEY = /^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$/;

function formatTime(value: string) {
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.valueOf()) ? value : timestamp.toLocaleString();
}

export function SkillsPage({
  onViewSkill,
}: {
  onViewSkill: (id: string) => void;
}) {
  const skills = useSkills();
  const create = useCreateSkill();
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
    if (!SKILL_KEY.test(normalizedKey)) {
      setValidationError(
        "Skill key must be lowercase and use dot or hyphen separators.",
      );
      return;
    }
    if (
      normalizedKey.length > MAX_KEY_LENGTH ||
      normalizedName.length > MAX_DISPLAY_NAME_LENGTH ||
      normalizedDescription.length > MAX_DESCRIPTION_LENGTH
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
      { onSuccess: (skill) => onViewSkill(skill.id) },
    );
  }

  const items = skills.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <section>
      <h2>Skills</h2>
      <p>
        Skills shape reasoning context. They never grant execution authority.
      </p>

      <h3>Create Skill</h3>
      <form onSubmit={submit} aria-label="Create Skill">
        <label htmlFor="skill-key">Key</label>
        <input
          id="skill-key"
          value={key}
          maxLength={MAX_KEY_LENGTH}
          onChange={(event) => setKey(event.target.value)}
          required
        />
        <label htmlFor="skill-display-name">Display name</label>
        <input
          id="skill-display-name"
          value={displayName}
          maxLength={MAX_DISPLAY_NAME_LENGTH}
          onChange={(event) => setDisplayName(event.target.value)}
          required
        />
        <label htmlFor="skill-description">Description</label>
        <textarea
          id="skill-description"
          value={description}
          maxLength={MAX_DESCRIPTION_LENGTH}
          onChange={(event) => setDescription(event.target.value)}
        />
        <button type="submit" disabled={create.isPending}>
          Create Skill
        </button>
      </form>
      {validationError && <p role="alert">{validationError}</p>}
      {create.isError && <p role="alert">Failed to create Skill.</p>}

      <h3>Skill registry</h3>
      {skills.isLoading && <p>Loading Skills...</p>}
      {skills.isError && <p role="alert">Failed to load Skills.</p>}
      {!skills.isLoading && !skills.isError && items.length === 0 && (
        <p>
          No Skills yet. Create one to begin its immutable revision history.
        </p>
      )}
      <ul aria-label="Skill registry">
        {items.map((skill) => (
          <li key={skill.id}>
            <article aria-label={`Skill ${skill.display_name}`}>
              <h4>
                <button type="button" onClick={() => onViewSkill(skill.id)}>
                  {skill.display_name}
                </button>
              </h4>
              <dl>
                <dt>Key</dt>
                <dd>{skill.key}</dd>
                <dt>Status</dt>
                <dd>{skill.status}</dd>
                <dt>Selected revision pointer</dt>
                <dd>{skill.active_revision_id ?? "No selected revision"}</dd>
                <dt>Created</dt>
                <dd>{formatTime(skill.created_at)}</dd>
                <dt>Updated</dt>
                <dd>{formatTime(skill.updated_at)}</dd>
              </dl>
            </article>
          </li>
        ))}
      </ul>
      {skills.hasNextPage && (
        <button
          type="button"
          disabled={skills.isFetchingNextPage}
          onClick={() => void skills.fetchNextPage()}
        >
          {skills.isFetchingNextPage
            ? "Loading more Skills..."
            : "Load more Skills"}
        </button>
      )}
    </section>
  );
}
