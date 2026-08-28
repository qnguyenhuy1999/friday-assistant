import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

function renderApp() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );
}

describe("App", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/");
    vi.spyOn(global, "fetch").mockImplementation(
      async () =>
        new Response(JSON.stringify({ items: [], next_cursor: null }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders the shell and conversation view by default", async () => {
    renderApp();
    expect(
      screen.getByRole("heading", { name: "Friday Agent OS" }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: "Conversation" }),
    ).toBeInTheDocument();
  });

  it("routes ?view=approvals&id=... to the run-scoped approvals view", async () => {
    window.history.replaceState({}, "", "/?view=approvals&id=r-1");
    renderApp();
    expect(
      await screen.findByRole("heading", { name: "Approvals" }),
    ).toBeInTheDocument();
  });

  it("navigates to the first-class Agents registry", async () => {
    renderApp();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Agents" }));
    expect(
      await screen.findByRole("heading", { name: "Agents" }),
    ).toBeInTheDocument();
    expect(window.location.search).toBe("?view=agents");
  });

  it("navigates to the first-class Workflows registry and exact detail route", async () => {
    renderApp();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Workflows" }));
    expect(
      await screen.findByRole("heading", { name: "Workflows" }),
    ).toBeInTheDocument();
    expect(window.location.search).toBe("?view=workflows");
  });

  it("navigates to the first-class Skills registry", async () => {
    renderApp();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Skills" }));
    expect(
      await screen.findByRole("heading", { name: "Skills" }),
    ).toBeInTheDocument();
    expect(window.location.search).toBe("?view=skills");
  });

  it("reads and navigates a Workflow detail route", async () => {
    window.history.replaceState({}, "", "/?view=workflow&id=w-1");
    renderApp();
    expect(
      await screen.findByText("Failed to load Workflow."),
    ).toBeInTheDocument();
  });

  it("reads an exact Task detail route", async () => {
    vi.restoreAllMocks();
    vi.spyOn(global, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/v1/tasks/t-1"))
        return new Response(
          JSON.stringify({
            id: "t-1",
            title: "Ship it",
            description: "A task to inspect.",
            status: "active",
            created_at: "2026-01-01T00:00:00Z",
            failure: null,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      if (
        url.endsWith("/v1/tasks/t-1/agent") ||
        url.endsWith("/v1/tasks/t-1/workflow")
      )
        return new Response("null", {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      return new Response(JSON.stringify({ items: [], next_cursor: null }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    window.history.replaceState({}, "", "/?view=task&id=t-1");
    renderApp();
    expect(
      await screen.findByRole("heading", { name: "Ship it" }),
    ).toBeInTheDocument();
    expect(window.location.search).toBe("?view=task&id=t-1");
  });

  it("navigates back from Workflow detail to the registry", async () => {
    vi.restoreAllMocks();
    vi.spyOn(global, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/workflows/w-1"))
        return new Response(
          JSON.stringify({
            id: "w-1",
            key: "release.pipeline",
            display_name: "Release pipeline",
            description: "",
            status: "active",
            active_revision_id: null,
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      if (url.includes("/revisions?"))
        return new Response("[]", {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      return new Response(JSON.stringify({ items: [], next_cursor: null }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    window.history.replaceState({}, "", "/?view=workflow&id=w-1");
    renderApp();
    await screen.findByRole("heading", { name: "Release pipeline" });
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Back to Workflows" }));
    expect(
      await screen.findByRole("heading", { name: "Workflows" }),
    ).toBeInTheDocument();
    expect(window.location.search).toBe("?view=workflows");
  });

  it("renders no detail view when the route carries no id", () => {
    window.history.replaceState({}, "", "/?view=run");
    renderApp();
    expect(screen.queryByText(/^Status:/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Approvals" }),
    ).not.toBeInTheDocument();
  });
});
