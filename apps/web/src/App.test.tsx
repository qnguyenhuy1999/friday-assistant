import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
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

  it("renders the shell and the tasks view by default", async () => {
    renderApp();
    expect(
      screen.getByRole("heading", { name: "Friday Agent OS" }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: "Tasks" }),
    ).toBeInTheDocument();
  });

  it("routes ?view=approvals&id=... to the run-scoped approvals view", async () => {
    window.history.replaceState({}, "", "/?view=approvals&id=r-1");
    renderApp();
    expect(
      await screen.findByRole("heading", { name: "Approvals" }),
    ).toBeInTheDocument();
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
