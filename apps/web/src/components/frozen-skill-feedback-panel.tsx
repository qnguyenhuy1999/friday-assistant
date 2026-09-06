import type { RunSkillAuditItem, SkillFeedback } from "@friday/contracts";
import { useState, type FormEvent } from "react";
import {
  useAddRunSkillFeedback,
  useRunSkillFeedback,
  type SkillFeedbackRating,
} from "../hooks/use-run-skill-feedback";

const MAX_CREATED_BY_LENGTH = 128;
const MAX_NOTE_LENGTH = 4000;
const RATINGS: SkillFeedbackRating[] = ["helpful", "neutral", "harmful"];

function isSkillFeedbackRating(value: string): value is SkillFeedbackRating {
  return RATINGS.includes(value as SkillFeedbackRating);
}

function formatTime(value: string): string {
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.valueOf()) ? value : timestamp.toLocaleString();
}

function feedbackMatchesFrozenSkill(
  feedback: SkillFeedback,
  runId: string,
  skill: RunSkillAuditItem,
): boolean {
  return (
    feedback.run_id === runId &&
    feedback.skill_id === skill.skill_id &&
    feedback.revision_id === skill.revision_id
  );
}

function SkillFeedbackList({ feedback }: { feedback: SkillFeedback[] }) {
  return (
    <ol aria-label="Operator feedback records">
      {feedback.map((item) => (
        <li key={item.id}>
          <article aria-label={`Feedback record ${item.id}`}>
            <h6>Feedback record {item.id}</h6>
            <dl>
              <dt>Rating</dt>
              <dd>{item.rating}</dd>
              <dt>Note</dt>
              <dd style={{ whiteSpace: "pre-wrap" }}>{item.note}</dd>
              <dt>Created by</dt>
              <dd>{item.created_by}</dd>
              <dt>Created at</dt>
              <dd>{formatTime(item.created_at)}</dd>
              <dt>Run ID</dt>
              <dd>{item.run_id}</dd>
              <dt>Skill ID</dt>
              <dd>{item.skill_id}</dd>
              <dt>Frozen revision ID</dt>
              <dd>{item.revision_id}</dd>
            </dl>
          </article>
        </li>
      ))}
    </ol>
  );
}

export function FrozenSkillFeedbackPanel({
  runId,
  skill,
}: {
  runId: string;
  skill: RunSkillAuditItem;
}) {
  const feedback = useRunSkillFeedback(runId, skill.skill_id);
  const addFeedback = useAddRunSkillFeedback(runId, skill.skill_id);
  const [rating, setRating] = useState<SkillFeedbackRating>("neutral");
  const [createdBy, setCreatedBy] = useState("");
  const [note, setNote] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const inputPrefix = `feedback-${runId}-${skill.skill_id}-${skill.revision_id}`;
  const feedbackItems = feedback.data ?? [];
  const provenanceMismatch =
    feedback.isSuccess &&
    feedbackItems.some(
      (item) => !feedbackMatchesFrozenSkill(item, runId, skill),
    );

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!createdBy.trim()) {
      setValidationError("Created by is required.");
      return;
    }
    if (createdBy.length > MAX_CREATED_BY_LENGTH) {
      setValidationError("Created by exceeds the supported length.");
      return;
    }
    if (note.length > MAX_NOTE_LENGTH) {
      setValidationError("Note exceeds the supported length.");
      return;
    }
    setValidationError(null);
    addFeedback.mutate(
      { rating, created_by: createdBy, note },
      {
        onSuccess: () => {
          setCreatedBy("");
          setNote("");
          setValidationError(null);
        },
      },
    );
  }

  return (
    <section aria-labelledby={`${inputPrefix}-heading`}>
      <h5 id={`${inputPrefix}-heading`}>
        Feedback for Skill {skill.skill_key}
      </h5>
      <p>
        Operator feedback is an annotation on this exact frozen Skill use. It
        does not change the Run outcome, frozen revision, or historical
        evidence. Submitting feedback does not change the Skill or trigger an
        improvement action.
      </p>
      {provenanceMismatch ? (
        <p role="alert">Feedback provenance could not be verified.</p>
      ) : (
        <>
          {feedback.isLoading && <p>Loading Skill feedback...</p>}
          {feedback.isError && (
            <p role="alert">Failed to load Skill feedback.</p>
          )}
          {!feedback.isLoading &&
            !feedback.isError &&
            feedbackItems.length === 0 && (
              <p>No operator feedback recorded for this frozen Skill use.</p>
            )}
          {!feedback.isLoading &&
            !feedback.isError &&
            feedbackItems.length > 0 && (
              <SkillFeedbackList feedback={feedbackItems} />
            )}
          <form
            onSubmit={submit}
            aria-label={`Feedback for Skill ${skill.skill_key}`}
          >
            <label htmlFor={`${inputPrefix}-rating`}>Rating</label>
            <select
              id={`${inputPrefix}-rating`}
              value={rating}
              onChange={(event) => {
                if (isSkillFeedbackRating(event.target.value))
                  setRating(event.target.value);
              }}
              required
            >
              {RATINGS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
            <label htmlFor={`${inputPrefix}-created-by`}>Created by</label>
            <input
              id={`${inputPrefix}-created-by`}
              value={createdBy}
              maxLength={MAX_CREATED_BY_LENGTH}
              onChange={(event) => setCreatedBy(event.target.value)}
              required
            />
            <label htmlFor={`${inputPrefix}-note`}>Note</label>
            <textarea
              id={`${inputPrefix}-note`}
              value={note}
              maxLength={MAX_NOTE_LENGTH}
              onChange={(event) => setNote(event.target.value)}
            />
            <button type="submit" disabled={addFeedback.isPending}>
              Submit feedback
            </button>
          </form>
          {validationError && <p role="alert">{validationError}</p>}
          {addFeedback.isError && (
            <p role="alert">
              Failed to add Skill feedback. The draft remains unchanged.
            </p>
          )}
        </>
      )}
    </section>
  );
}
