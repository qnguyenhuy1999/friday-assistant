import { ApprovalsPage } from "./pages/approvals-page";
import { RunDetailPage } from "./pages/run-detail-page";
import { TasksPage } from "./pages/tasks-page";
import { SchedulesPage } from "./pages/schedules-page";
import { useRoute } from "./router/use-route";
export function App() {
  const [route, navigate] = useRoute();
  return (
    <main>
      <h1>Friday Agent OS</h1>
      {route.view === "tasks" && (
        <TasksPage
          onRunStarted={(id) => navigate({ view: "run", id })}
          onViewSchedules={(id) => navigate({ view: "schedules", id })}
        />
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
      {route.view === "schedules" && route.id && (
        <SchedulesPage
          taskId={route.id}
          onBack={() => navigate({ view: "tasks", id: null })}
        />
      )}
    </main>
  );
}
