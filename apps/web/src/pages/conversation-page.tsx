import { useCallback, useEffect, useRef, useState } from "react";
import { ConversationTranscript } from "../components/conversation-transcript";
import { VoiceControls } from "../components/voice-controls";
import { isPushToTalkKey } from "../voice/push-to-talk-key";
import {
  useConversationId,
  useConversationTurns,
  useSubmitConversationTurn,
} from "../hooks/use-conversation";
import { useConversationAnswers } from "../hooks/use-conversation-answers";
import type { TurnAnswer } from "../hooks/use-turn-answer";
import { useVoice } from "../hooks/use-voice";
import { friday } from "../friday-client";
export function ConversationPage({
  onReviewApproval,
}: {
  onReviewApproval(runId: string): void;
}) {
  const { conversationId, isError } = useConversationId();
  const turns = useConversationTurns(conversationId);
  const submit = useSubmitConversationTurn(conversationId);
  const answers = useConversationAnswers(conversationId, turns.items);
  const [text, setText] = useState("");
  const [cancelError, setCancelError] = useState<string | null>(null);
  const activeRunId = useRef<string | null>(null);
  const speakableRunIds = useRef(new Set<string>());
  /** Bumped by every interruption. A submission that resolves against a stale
   * generation started before the user pressed Escape, so its run must be
   * cancelled rather than adopted — otherwise an interrupted turn keeps
   * running and still speaks its answer. */
  const submitGeneration = useRef(0);
  const cancelRun = useCallback((runId: string) => {
    void friday.runs.cancel(runId).catch(() => {
      // Local suppression remains authoritative, but durable cancellation
      // failure needs to be visible so the user can make an informed retry.
      setCancelError("Could not cancel the run on the server.");
    });
  }, []);
  const adoptRun = useCallback(
    (runId: string, generation: number) => {
      if (generation !== submitGeneration.current) {
        cancelRun(runId);
        return;
      }
      activeRunId.current = runId;
      speakableRunIds.current.add(runId);
    },
    [cancelRun],
  );
  const voice = useVoice(async (input) => {
    const generation = submitGeneration.current;
    const turn = await submit.mutateAsync({
      client_turn_id: crypto.randomUUID(),
      input_text: input.text,
      input_mode: input.inputMode,
      recognition_language: input.language,
    });
    adoptRun(turn.run_id, generation);
  });
  const { deliverAnswer } = voice;
  const cancelActiveRun = useCallback(() => {
    const runId = activeRunId.current;
    // Commit the local interruption before the durable cancellation request:
    // a worker may win that request's race, but it must never revive output.
    submitGeneration.current += 1;
    activeRunId.current = null;
    if (runId) speakableRunIds.current.delete(runId);
    voice.controller.interrupt();
    if (runId) cancelRun(runId);
  }, [voice.controller, cancelRun]);
  /** Only a run this page started drives the voice session: answers hydrated
   * from history must not speak, nor resume a hands-free session nobody asked
   * for. A run that ends without an answer — failed, cancelled — still has to
   * release the session, or the turn never finishes. */
  const handleAnswerState = useCallback(
    (runId: string, answer: TurnAnswer) => {
      if (activeRunId.current !== runId) return;
      if (answer.state === "pending") return;
      if (answer.state === "awaiting_approval") {
        voice.controller.notifyAwaitingApproval();
        return;
      }
      activeRunId.current = null;
      const speakable = speakableRunIds.current.delete(runId);
      if (speakable && answer.state === "answered" && answer.summary)
        deliverAnswer(answer.summary);
      else voice.controller.notifyResultDelivered();
    },
    [deliverAnswer, voice.controller],
  );
  useEffect(() => {
    const down = (event: KeyboardEvent) => {
      if (isPushToTalkKey(event)) {
        event.preventDefault();
        voice.controller.pressToTalk();
      }
    };
    const up = (event: KeyboardEvent) => {
      if (isPushToTalkKey(event)) {
        event.preventDefault();
        voice.controller.releaseToTalk();
      }
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") cancelActiveRun();
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("keydown", escape);
    };
  }, [voice.controller, cancelActiveRun]);
  const inputBlocked = [
    "finalizing",
    "submitting",
    "processing",
    "awaiting_approval",
  ].includes(voice.snapshot.state);
  /** Typed turns go through the controller too, so one run has one lifecycle
   * regardless of how it was started: the same submit fence, the same
   * processing state, the same header. */
  const send = () => {
    if (inputBlocked || !text.trim()) return;
    void voice.controller.submitTyped(text);
    setText("");
  };
  if (isError)
    return (
      <section>
        <h2>Conversation</h2>
        <p role="alert">Conversation unavailable. Try again.</p>
      </section>
    );
  return (
    <section>
      <h2>Conversation</h2>
      <p>
        {voice.snapshot.state === "listening"
          ? "Listening…"
          : voice.snapshot.state === "speaking"
            ? "Speaking…"
            : voice.snapshot.state === "awaiting_approval"
              ? "Approval required"
              : "Ready"}
      </p>
      {cancelError && <p role="alert">{cancelError}</p>}
      <ConversationTranscript
        turns={turns.items}
        answers={answers}
        onShowEarlier={
          turns.hasNextPage ? () => void turns.fetchNextPage() : undefined
        }
        onReviewApproval={onReviewApproval}
        onAnswerState={handleAnswerState}
      />
      <label>
        Message{" "}
        <input
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              send();
            }
          }}
        />
      </label>
      <button
        type="button"
        onClick={send}
        disabled={!conversationId || submit.isPending || inputBlocked}
      >
        Send
      </button>
      <VoiceControls
        capabilities={voice.capabilities}
        snapshot={voice.snapshot}
        preferences={voice.preferences}
        disabled={!conversationId}
        onPreferences={voice.setPreferences}
        onPress={() => voice.controller.pressToTalk()}
        onRelease={() => voice.controller.releaseToTalk()}
        onHandsFree={(enabled) => voice.controller.setHandsFree(enabled)}
      />
    </section>
  );
}
