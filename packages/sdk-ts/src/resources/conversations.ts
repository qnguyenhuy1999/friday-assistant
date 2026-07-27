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
  /** `recent_desc` opens on the newest turns and pages backwards in time, so a
   * long conversation does not have to be downloaded from the beginning.
   * Cursors are bound to their ordering and cannot be mixed. */
  order?: "created_at_id_asc" | "recent_desc";
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
      query: {
        limit: params.limit,
        cursor: params.cursor,
        order: params.order,
      },
      validate: validateConversationTurnPage,
    });
  }
}
