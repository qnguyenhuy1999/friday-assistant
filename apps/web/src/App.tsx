import { ApprovalsPage } from "./pages/approvals-page";
import { RunDetailPage } from "./pages/run-detail-page";
import { TasksPage } from "./pages/tasks-page";
import { SchedulesPage } from "./pages/schedules-page";
import { ConversationPage } from "./pages/conversation-page";
import { AgentDetailPage } from "./pages/agent-detail-page";
import { AgentsPage } from "./pages/agents-page";
import { useRoute } from "./router/use-route";
export function App() {
  const [route, navigate] = useRoute();
  return (
    <main>
      <h1>Friday Agent OS</h1>
      <nav>
        <button
          type="button"
          onClick={() => navigate({ view: "conversation", id: null })}
        >
          Conversation
        </button>
        <button
          type="button"
          onClick={() => navigate({ view: "tasks", id: null })}
        >
          Tasks
        </button>
        <button
          type="button"
          onClick={() => navigate({ view: "agents", id: null })}
        >
          Agents
        </button>
      </nav>
      {route.view === "conversation" && (
        <ConversationPage
          onReviewApproval={(id) => navigate({ view: "approvals", id })}
        />
      )}
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
          onViewRun={(id) => navigate({ view: "run", id })}
        />
      )}
      {route.view === "agents" && (
        <AgentsPage onViewAgent={(id) => navigate({ view: "agent", id })} />
      )}
      {route.view === "agent" && route.id && (
        <AgentDetailPage
          agentId={route.id}
          onBack={() => navigate({ view: "agents", id: null })}
        />
      )}
    </main>
  );
}
