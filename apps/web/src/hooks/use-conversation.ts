import type { SubmitConversationTurnBody } from "@friday/contracts";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { friday } from "../friday-client";
import { loadAllPages } from "./use-all-pages";
export const CONVERSATION_STORAGE_KEY = "conversation.id";
export const conversationTurnsQueryKey = (id: string) =>
  ["conversation-turns", id] as const;
export function useConversationId() {
  const [conversationId, setId] = useState(() =>
    localStorage.getItem(CONVERSATION_STORAGE_KEY),
  );
  const [isError, setError] = useState(false);
  useEffect(() => {
    if (conversationId) return;
    let alive = true;
    friday.conversations
      .create()
      .then((conversation) => {
        if (alive) {
          localStorage.setItem(CONVERSATION_STORAGE_KEY, conversation.id);
          setId(conversation.id);
        }
      })
      .catch(() => alive && setError(true));
    return () => {
      alive = false;
    };
  }, [conversationId]);
  return { conversationId, isError };
}
export function useConversationTurns(id: string | null) {
  return useQuery({
    queryKey: conversationTurnsQueryKey(id ?? ""),
    enabled: id !== null,
    queryFn: () =>
      loadAllPages((cursor) =>
        friday.conversations.listTurns(String(id), { limit: 100, cursor }),
      ),
  });
}
export function useSubmitConversationTurn(id: string | null) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: SubmitConversationTurnBody) =>
      friday.conversations.submitTurn(String(id), body),
    onSuccess: () =>
      client.invalidateQueries({
        queryKey: conversationTurnsQueryKey(id ?? ""),
      }),
  });
}
