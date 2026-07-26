/** Mirrors `apps/api/schemas/conversations.py`. */
export type ConversationInputMode = "typed" | "push_to_talk" | "hands_free";

export interface SubmitConversationTurnBody {
  client_turn_id: string;
  input_text: string;
  input_mode: ConversationInputMode;
  recognition_language: string | null;
}
