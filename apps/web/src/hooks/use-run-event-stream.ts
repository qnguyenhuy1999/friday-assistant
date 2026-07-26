import type { RunEvent } from "@friday/contracts";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { friday } from "../friday-client";
import { runArtifactsQueryKey } from "./use-artifacts";
import { runApprovalsQueryKey } from "./use-approvals";
import { runQueryKey } from "./use-run";
import { runStepsQueryKey } from "./use-run-steps";
import { runToolInvocationsQueryKey } from "./use-tool-invocations";
function merge(current: RunEvent[], incoming: RunEvent[]) {
  const map = new Map(current.map((e) => [e.event_id, e]));
  incoming.forEach((e) => map.set(e.event_id, e));
  return [...map.values()].sort((a, b) => a.sequence - b.sequence);
}
export function useRunEventStream(runId: string): RunEvent[] {
  const q = useQueryClient();
  const [events, setEvents] = useState<RunEvent[]>([]);
  useEffect(() => {
    let cancelled = false;
    setEvents([]);
    friday.events
      .listForRun(runId, { limit: 100 })
      .then((page) => {
        if (!cancelled) setEvents((old) => merge(old, page.items));
      })
      .catch(() => {});
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
    return () => {
      cancelled = true;
      off();
      stream.close();
    };
  }, [q, runId]);
  return events;
}
