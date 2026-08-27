import type {
  CreateAgentBody,
  CreateAgentRevisionBody,
} from "@friday/contracts";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { friday } from "../friday-client";

export const agentsQueryKey = ["agents"] as const;
export const agentQueryKey = (agentId: string) => ["agent", agentId] as const;
export const agentRevisionsQueryKey = (agentId: string) =>
  ["agent-revisions", agentId] as const;

function invalidateAgent(
  queryClient: ReturnType<typeof useQueryClient>,
  agentId: string,
) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: agentsQueryKey }),
    queryClient.invalidateQueries({ queryKey: agentQueryKey(agentId) }),
    queryClient.invalidateQueries({
      queryKey: agentRevisionsQueryKey(agentId),
    }),
  ]);
}

export function useAgents() {
  return useQuery({
    queryKey: agentsQueryKey,
    queryFn: () => friday.agents.list(),
  });
}

export function useAgent(agentId: string) {
  return useQuery({
    queryKey: agentQueryKey(agentId),
    queryFn: () => friday.agents.get(agentId),
  });
}

export function useAgentRevisions(agentId: string) {
  return useQuery({
    queryKey: agentRevisionsQueryKey(agentId),
    queryFn: () => friday.agents.listRevisions(agentId),
  });
}

export function useCreateAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateAgentBody) => friday.agents.create(input),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: agentsQueryKey }),
  });
}

export function useCreateAgentRevision(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateAgentRevisionBody) =>
      friday.agents.createRevision(agentId, input),
    onSuccess: () => invalidateAgent(queryClient, agentId),
  });
}

export function useActivateAgentRevision(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (revisionId: string) =>
      friday.agents.activateRevision(agentId, revisionId),
    onSuccess: () => invalidateAgent(queryClient, agentId),
  });
}

export function useAgentLifecycle(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (action: "disable" | "archive") =>
      friday.agents[action](agentId),
    onSuccess: () => invalidateAgent(queryClient, agentId),
  });
}
