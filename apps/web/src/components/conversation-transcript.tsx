import { useEffect } from "react";
import type { ConversationTurn } from "@friday/contracts";
import { useTurnAnswer } from "../hooks/use-turn-answer";
function Turn({
  turn,
  onReviewApproval,
  onAnswer,
}: {
  turn: ConversationTurn;
  onReviewApproval(runId: string): void;
  onAnswer(summary: string): void;
}) {
  const answer = useTurnAnswer(turn.run_id);
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
export function ConversationTranscript({
  turns,
  onReviewApproval,
  onAnswer = () => undefined,
}: {
  turns: ConversationTurn[];
  onReviewApproval(runId: string): void;
  onAnswer?(summary: string): void;
}) {
  if (!turns.length) return <p>Ask Friday to get started.</p>;
  return (
    <ol>
      {turns.map((turn) => (
        <Turn
          key={turn.id}
          turn={turn}
          onReviewApproval={onReviewApproval}
          onAnswer={onAnswer}
        />
      ))}
    </ol>
  );
}
