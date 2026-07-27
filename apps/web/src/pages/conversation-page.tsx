import { useEffect, useState } from "react";
import type { ConversationTurn } from "@friday/contracts";
import { ConversationTranscript } from "../components/conversation-transcript";
import { VoiceControls } from "../components/voice-controls";
import { isPushToTalkKey } from "../voice/push-to-talk-key";
import {
  useConversationId,
  useConversationTurns,
  useSubmitConversationTurn,
} from "../hooks/use-conversation";
import { useConversationAnswers } from "../hooks/use-conversation-answers";
import { useVoice } from "../hooks/use-voice";
/** Stable identity for the pre-load case, so the answer window's memos do not
 * see a fresh array on every render. */
const NO_TURNS: ConversationTurn[] = [];
export function ConversationPage() {
  const { conversationId, isError } = useConversationId();
  const turns = useConversationTurns(conversationId);
  const submit = useSubmitConversationTurn(conversationId);
  const { visibleTurns, answers, earlierCount, showEarlier } =
    useConversationAnswers(conversationId, turns.data?.items ?? NO_TURNS);
  const [text, setText] = useState("");
  const voice = useVoice(async (input) => {
    await submit.mutateAsync({
      client_turn_id: crypto.randomUUID(),
      input_text: input.text,
      input_mode: input.inputMode,
      recognition_language: input.language,
    });
  });
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
      if (event.key === "Escape") voice.controller.interrupt();
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("keydown", escape);
    };
  }, [voice.controller]);
  const send = () => {
    const input_text = text.trim();
    if (!input_text) return;
    submit.mutate({
      client_turn_id: crypto.randomUUID(),
      input_text,
      input_mode: "typed",
      recognition_language: null,
    });
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
      <ConversationTranscript
        turns={visibleTurns}
        answers={answers}
        earlierCount={earlierCount}
        onShowEarlier={showEarlier}
        onReviewApproval={() => undefined}
        onAnswer={voice.deliverAnswer}
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
        disabled={!conversationId || submit.isPending}
      >
        Send
      </button>
      <VoiceControls
        capabilities={voice.capabilities}
        snapshot={voice.snapshot}
        preferences={voice.preferences}
        onPreferences={voice.setPreferences}
        onPress={() => voice.controller.pressToTalk()}
        onRelease={() => voice.controller.releaseToTalk()}
        onHandsFree={(enabled) => voice.controller.setHandsFree(enabled)}
      />
    </section>
  );
}
