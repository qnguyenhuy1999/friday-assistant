import type { SubmitConversationTurnBody } from "@friday/contracts";
import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { friday } from "../friday-client";
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
/** Turns fetched per page. A conversation only grows, so the transcript opens
 * on its newest page and walks backwards on demand rather than downloading the
 * whole history — which is what `loadAllPages` did here before. */
export const TURN_PAGE_SIZE = 25;

export function useConversationTurns(id: string | null) {
  const query = useInfiniteQuery({
    queryKey: conversationTurnsQueryKey(id ?? ""),
    enabled: id !== null,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      friday.conversations.listTurns(String(id), {
        limit: TURN_PAGE_SIZE,
        cursor: pageParam,
        order: "recent_desc",
      }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  // Pages arrive newest-first while each page is oldest-first internally, so
  // reversing the page order (not the items) rebuilds one chronological list.
  const items = useMemo(
    () =>
      (query.data?.pages ?? []).flatMap(
        (_, index, pages) => pages[pages.length - 1 - index]!.items,
      ),
    [query.data],
  );
  return { ...query, items };
}
export function useSubmitConversationTurn(id: string | null) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: SubmitConversationTurnBody) =>
      friday.conversations.submitTurn(String(id), body),
    onSuccess: () => {
      // Let the submitter adopt the returned run before a refetch can mount a
      // terminal turn. The refetch still happens immediately, but is not part
      // of mutation completion.
      void client.invalidateQueries({
        queryKey: conversationTurnsQueryKey(id ?? ""),
      });
    },
  });
}
