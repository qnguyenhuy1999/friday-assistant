import type { RunEvent } from "@friday/contracts";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { friday } from "../friday-client";
import { runArtifactsQueryKey } from "./use-artifacts";
import { runApprovalsQueryKey } from "./use-approvals";
import { runQueryKey } from "./use-run";
import { runStepsQueryKey } from "./use-run-steps";
import { runToolInvocationsQueryKey } from "./use-tool-invocations";
import { loadAllPages } from "./use-all-pages";
function merge(current: RunEvent[], incoming: RunEvent[]) {
  const map = new Map(current.map((e) => [e.event_id, e]));
  incoming.forEach((e) => map.set(e.event_id, e));
  return [...map.values()].sort((a, b) => a.sequence - b.sequence);
}
export type RunEventStreamState = {
  events: RunEvent[];
  isLoading: boolean;
  isDegraded: boolean;
};
export function useRunEventStream(runId: string): RunEventStreamState {
  const q = useQueryClient();
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isDegraded, setIsDegraded] = useState(false);
  useEffect(() => {
    let cancelled = false;
    setEvents([]);
    setIsLoading(true);
    setIsDegraded(false);
    const backfill = () =>
      loadAllPages((cursor) =>
        friday.events.listForRun(runId, { limit: 100, cursor }),
      )
        .then((page) => {
          if (!cancelled) {
            setEvents((old) => merge(old, page.items));
            setIsLoading(false);
          }
        })
        .catch(() => {
          if (!cancelled) {
            setIsLoading(false);
            setIsDegraded(true);
          }
        });
    void backfill();
    const stream = friday.events.streamForRun(runId);
    const off = stream.onEvent((event) => {
      setEvents((old) => merge(old, [event]));
      q.invalidateQueries({ queryKey: runQueryKey(runId) });
      if (event.type.startsWith("step_"))
        q.invalidateQueries({ queryKey: runStepsQueryKey(runId) });
      if (event.type.startsWith("tool_invocation_"))
        q.invalidateQueries({ queryKey: runToolInvocationsQueryKey(runId) });
      if (event.type === "artifact_created")
        q.invalidateQueries({ queryKey: runArtifactsQueryKey(runId) });
      if (event.type.startsWith("approval_"))
        q.invalidateQueries({ queryKey: runApprovalsQueryKey(runId) });
    });
    const offError = stream.onError(() => {
      if (!cancelled) setIsDegraded(true);
    });
    // EventSource reconnects itself, but this ensures the control-plane data
    // still progresses when the stream cannot be maintained.
    const fallbackPoll = window.setInterval(() => void backfill(), 5_000);
    return () => {
      cancelled = true;
      off();
      offError();
      window.clearInterval(fallbackPoll);
      stream.close();
    };
  }, [q, runId]);
  return { events, isLoading, isDegraded };
}
