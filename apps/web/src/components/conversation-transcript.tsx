import { useEffect, useRef } from "react";
import type { ConversationTurn } from "@friday/contracts";
import { PENDING_TURN_ANSWER, type TurnAnswer } from "../hooks/use-turn-answer";
function Turn({
  turn,
  answer,
  onReviewApproval,
  onAnswerState,
  onRetry,
}: {
  turn: ConversationTurn;
  answer: TurnAnswer;
  onReviewApproval(runId: string): void;
  onAnswerState(runId: string, answer: TurnAnswer): void;
  onRetry(runId: string): void;
}) {
  const previousState = useRef(answer.state);
  // Every state a run reaches is reported, not just a successful answer: the
  // voice session is waiting on this run, and a failure or an approval pause
  // that goes unreported leaves it stuck mid-turn.
  useEffect(() => {
    if (previousState.current !== answer.state) {
      previousState.current = answer.state;
      onAnswerState(turn.run_id, answer);
    }
  }, [answer, onAnswerState, turn.run_id]);
  return (
    <li>
      <strong>You</strong>
      <p>{turn.input_text}</p>
      {answer.state === "answered" && (
        <>
          <strong>Friday</strong>
          <p>{answer.summary}</p>
          {answer.details !== null && (
            <pre>
              {typeof answer.details === "string"
                ? answer.details
                : JSON.stringify(answer.details, null, 2)}
            </pre>
          )}
        </>
      )}
      {answer.state === "awaiting_approval" && (
        <p role="alert">
          Approval required.{" "}
          <button
            type="button"
            onClick={() => onReviewApproval(answer.runId ?? turn.run_id)}
          >
            Review approval
          </button>
        </p>
      )}
      {answer.state === "failed" && (
        <p role="alert">
          {answer.summary}{" "}
          <button
            type="button"
            onClick={() => onRetry(answer.runId ?? turn.run_id)}
          >
            Retry
          </button>
        </p>
      )}
      {answer.state === "cancelled" && <p>Run was cancelled.</p>}
    </li>
  );
}
/** Renders turns against answers loaded in one bounded batch by the page, so a
 * long conversation does not fan out into a request per turn. */
export function ConversationTranscript({
  turns,
  answers,
  onReviewApproval,
  onAnswerState = () => undefined,
  onRetry,
  onShowEarlier,
}: {
  turns: ConversationTurn[];
  answers: ReadonlyMap<string, TurnAnswer>;
  onReviewApproval(runId: string): void;
  onAnswerState?(runId: string, answer: TurnAnswer): void;
  onRetry(runId: string): void;
  /** Omitted when nothing earlier remains. The count is deliberately not shown:
   * older turns live behind a cursor, so how many there are is not known until
   * they are fetched. */
  onShowEarlier?(): void;
}) {
  if (!turns.length) return <p>Ask Friday to get started.</p>;
  return (
    <>
      {onShowEarlier && (
        <button type="button" onClick={onShowEarlier}>
          Show earlier turns
        </button>
      )}
      <ol>
        {turns.map((turn) => (
          <Turn
            key={turn.id}
            turn={turn}
            answer={answers.get(turn.run_id) ?? PENDING_TURN_ANSWER}
            onReviewApproval={onReviewApproval}
            onAnswerState={onAnswerState}
            onRetry={onRetry}
          />
        ))}
      </ol>
    </>
  );
}
