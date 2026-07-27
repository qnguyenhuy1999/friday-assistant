import { useEffect } from "react";
import type { ConversationTurn } from "@friday/contracts";
import { PENDING_TURN_ANSWER, type TurnAnswer } from "../hooks/use-turn-answer";
function Turn({
  turn,
  answer,
  onReviewApproval,
  onAnswer,
}: {
  turn: ConversationTurn;
  answer: TurnAnswer;
  onReviewApproval(runId: string): void;
  onAnswer(summary: string): void;
}) {
  useEffect(() => {
    if (answer.state === "answered" && answer.summary) onAnswer(answer.summary);
  }, [answer.state, answer.summary, onAnswer]);
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
          <button type="button" onClick={() => onReviewApproval(turn.run_id)}>
            Review approval
          </button>
        </p>
      )}
      {answer.state === "failed" && <p role="alert">{answer.summary}</p>}
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
  onAnswer = () => undefined,
  earlierCount = 0,
  onShowEarlier,
}: {
  turns: ConversationTurn[];
  answers: ReadonlyMap<string, TurnAnswer>;
  onReviewApproval(runId: string): void;
  onAnswer?(summary: string): void;
  earlierCount?: number;
  onShowEarlier?(): void;
}) {
  if (!turns.length) return <p>Ask Friday to get started.</p>;
  return (
    <>
      {earlierCount > 0 && onShowEarlier && (
        <button type="button" onClick={onShowEarlier}>
          Show {earlierCount} earlier {earlierCount === 1 ? "turn" : "turns"}
        </button>
      )}
      <ol>
        {turns.map((turn) => (
          <Turn
            key={turn.id}
            turn={turn}
            answer={answers.get(turn.run_id) ?? PENDING_TURN_ANSWER}
            onReviewApproval={onReviewApproval}
            onAnswer={onAnswer}
          />
        ))}
      </ol>
    </>
  );
}
