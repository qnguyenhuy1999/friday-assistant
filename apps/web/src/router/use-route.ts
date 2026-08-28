import { useCallback, useEffect, useState } from "react";
export type View =
  | "conversation"
  | "tasks"
  | "task"
  | "run"
  | "approvals"
  | "schedules"
  | "agents"
  | "agent"
  | "workflows"
  | "workflow";
export interface Route {
  view: View;
  id: string | null;
}
function readRoute(): Route {
  const p = new URLSearchParams(window.location.search);
  const view = p.get("view");
  return view === "conversation" ||
    view === "tasks" ||
    view === "task" ||
    view === "run" ||
    view === "approvals" ||
    view === "schedules" ||
    view === "agents" ||
    view === "agent" ||
    view === "workflows" ||
    view === "workflow"
    ? { view, id: p.get("id") }
    : { view: "conversation", id: null };
}
export function useRoute(): [Route, (route: Route) => void] {
  const [route, setRoute] = useState(readRoute);
  useEffect(() => {
    const handler = () => setRoute(readRoute());
    window.addEventListener("popstate", handler);
    return () => window.removeEventListener("popstate", handler);
  }, []);
  const navigate = useCallback((next: Route) => {
    const p = new URLSearchParams({ view: next.view });
    if (next.id) p.set("id", next.id);
    window.history.pushState({}, "", `${window.location.pathname}?${p}`);
    setRoute(next);
  }, []);
  return [route, navigate];
}
