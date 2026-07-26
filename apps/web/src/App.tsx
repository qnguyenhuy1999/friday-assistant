import { ApprovalsPage } from "./pages/approvals-page";
import { RunDetailPage } from "./pages/run-detail-page";
import { TasksPage } from "./pages/tasks-page";
import { useRoute } from "./router/use-route";
export function App() {
  const [route, navigate] = useRoute();
  return (
    <main>
      <h1>Friday Agent OS</h1>
      {route.view === "tasks" && (
        <TasksPage onRunStarted={(id) => navigate({ view: "run", id })} />
      )}
      {route.view === "run" && route.id && (
        <RunDetailPage
          runId={route.id}
          onViewApprovals={() => navigate({ view: "approvals", id: route.id })}
        />
      )}
      {route.view === "approvals" && route.id && (
        <ApprovalsPage
          runId={route.id}
          onBackToRun={() => navigate({ view: "run", id: route.id })}
        />
      )}
    </main>
  );
}
