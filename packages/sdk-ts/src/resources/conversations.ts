import type { Conversation, ConversationTurn, Page } from "@friday/contracts";
import {
  validateConversation,
  validateConversationTurn,
  validateConversationTurnPage,
} from "@friday/contracts";
import type { SubmitConversationTurnBody } from "@friday/contracts";
import type { FridayHttpClient } from "../http";

export interface ListConversationTurnsParams {
  limit?: number;
  cursor?: string;
}

export class ConversationsResource {
  constructor(private readonly http: FridayHttpClient) {}
  create() {
    return this.http.requestJson<Conversation>({
      method: "POST",
      path: "/v1/conversations",
      validate: validateConversation,
    });
  }
  get(id: string) {
    return this.http.requestJson<Conversation>({
      method: "GET",
      path: `/v1/conversations/${id}`,
      validate: validateConversation,
    });
  }
  submitTurn(id: string, body: SubmitConversationTurnBody) {
    return this.http.requestJson<ConversationTurn>({
      method: "POST",
      path: `/v1/conversations/${id}/turns`,
      body,
      validate: validateConversationTurn,
    });
  }
  listTurns(id: string, params: ListConversationTurnsParams = {}) {
    return this.http.requestJson<Page<ConversationTurn>>({
      method: "GET",
      path: `/v1/conversations/${id}/turns`,
      query: { limit: params.limit, cursor: params.cursor },
      validate: validateConversationTurnPage,
    });
  }
}
