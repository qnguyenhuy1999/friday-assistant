import type {
  Agent,
  AgentPage,
  AgentRevision,
  CreateAgentBody,
  CreateAgentRevisionBody,
} from "@friday/contracts";
import {
  validateAgent,
  validateAgentPage,
  validateAgentRevision,
  validateAgentRevisions,
} from "@friday/contracts";
import type { FridayHttpClient } from "../http";

export interface ListAgentsParams {
  limit?: number;
  cursor?: string;
}

export class AgentsResource {
  constructor(private readonly http: FridayHttpClient) {}
  create(input: CreateAgentBody) {
    return this.http.requestJson<Agent>({
      method: "POST",
      path: "/v1/agents",
      body: input,
      validate: validateAgent,
    });
  }
  list(params: ListAgentsParams = {}) {
    return this.http.requestJson<AgentPage>({
      method: "GET",
      path: "/v1/agents",
      query: { limit: params.limit, cursor: params.cursor },
      validate: validateAgentPage,
    });
  }
  get(agentId: string) {
    return this.http.requestJson<Agent>({
      method: "GET",
      path: `/v1/agents/${agentId}`,
      validate: validateAgent,
    });
  }
  createRevision(agentId: string, input: CreateAgentRevisionBody) {
    return this.http.requestJson<AgentRevision>({
      method: "POST",
      path: `/v1/agents/${agentId}/revisions`,
      body: input,
      validate: validateAgentRevision,
    });
  }
  listRevisions(agentId: string) {
    return this.http.requestJson<AgentRevision[]>({
      method: "GET",
      path: `/v1/agents/${agentId}/revisions`,
      validate: validateAgentRevisions,
    });
  }
  activateRevision(agentId: string, revisionId: string) {
    return this.http.requestJson<Agent>({
      method: "POST",
      path: `/v1/agents/${agentId}/revisions/${revisionId}/activate`,
      validate: validateAgent,
    });
  }
  disable(agentId: string) {
    return this.http.requestJson<Agent>({
      method: "POST",
      path: `/v1/agents/${agentId}/disable`,
      validate: validateAgent,
    });
  }
  archive(agentId: string) {
    return this.http.requestJson<Agent>({
      method: "POST",
      path: `/v1/agents/${agentId}/archive`,
      validate: validateAgent,
    });
  }
}
