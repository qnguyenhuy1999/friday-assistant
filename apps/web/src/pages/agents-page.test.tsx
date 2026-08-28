import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AgentsPage } from "./agents-page";

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const agent = {
  id: "a-1",
  key: "coder",
  display_name: "Coder",
  description: "Writes code",
  status: "active",
  active_revision_id: "r-1",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function renderPage(onViewAgent = vi.fn()) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <AgentsPage onViewAgent={onViewAgent} />
    </QueryClientProvider>,
  );
  return { onViewAgent };
}

describe("AgentsPage", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders Agent registry results and opens the exact Agent", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      response({ items: [agent], next_cursor: null }),
    );
    const { onViewAgent } = renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Coder" }));
    expect(screen.getByLabelText("Agent registry")).toHaveTextContent(
      "key: coder",
    );
    expect(screen.getByLabelText("Agent registry")).toHaveTextContent(
      "selected revision: r-1",
    );
    expect(onViewAgent).toHaveBeenCalledWith("a-1");
  });

  it("renders empty and SDK error states safely", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      response({ items: [], next_cursor: null }),
    );
    renderPage();
    expect(await screen.findByText(/No Agents yet/)).toBeInTheDocument();

    vi.restoreAllMocks();
    vi.spyOn(global, "fetch").mockResolvedValue(
      response({ error: { type: "unavailable", message: "no" } }, 503),
    );
    renderPage();
    expect((await screen.findAllByRole("alert")).at(-1)).toHaveTextContent(
      "Failed to load agents.",
    );
  });

  it("creates an Agent with the contract body and navigates to it", async () => {
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(response({ items: [], next_cursor: null }))
      .mockResolvedValueOnce(response(agent, 201));
    const { onViewAgent } = renderPage();
    const user = userEvent.setup();
    await screen.findByText(/No Agents yet/);
    await user.type(screen.getByLabelText("Key"), " coder ");
    await user.type(screen.getByLabelText("Display name"), " Coder ");
    await user.type(screen.getByLabelText("Description"), " Writes code ");
    await user.click(screen.getByRole("button", { name: "Create agent" }));
    expect(onViewAgent).toHaveBeenCalledWith("a-1");
    const request = fetchMock.mock.calls[1];
    expect(request).toBeDefined();
    expect(JSON.parse(String((request?.[1] as RequestInit).body))).toEqual({
      key: "coder",
      display_name: "Coder",
      description: "Writes code",
    });
  });

  it("loads additional Agent pages instead of stopping at the first page", async () => {
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(
        response({ items: [agent], next_cursor: "agent-page-2" }),
      )
      .mockResolvedValueOnce(
        response({
          items: [{ ...agent, id: "a-2", display_name: "Reviewer" }],
          next_cursor: null,
        }),
      );
    renderPage();
    expect(await screen.findByRole("button", { name: "Coder" })).toBeInTheDocument();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Load more Agents" }));
    expect(
      await screen.findByRole("button", { name: "Reviewer" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain(
      "cursor=agent-page-2",
    );
  });
});
