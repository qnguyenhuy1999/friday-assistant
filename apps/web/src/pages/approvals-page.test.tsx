import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApprovalsPage } from "./approvals-page";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const approval = {
  approval_id: "a-1",
  run_id: "r-1",
  step_id: null,
  category: "computer_use",
  summary: "Click Send",
  reason: "explicit sign-off",
  requested_action: "computer.click",
  requested_input: { pid: 1 },
  status: "pending",
  requested_at: "x",
  expires_at: null,
  resolved_at: null,
  resolution_note: null,
  resolver: null,
  authorization_fingerprint: null,
  consumed_at: null,
};

function renderPage() {
  const onBackToRun = vi.fn();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <ApprovalsPage runId="r-1" onBackToRun={onBackToRun} />
    </QueryClientProvider>,
  );
  return { onBackToRun };
}

describe("ApprovalsPage", () => {
  afterEach(() => vi.restoreAllMocks());

  it("opens the detail for the selected approval", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      jsonResponse({ items: [approval], next_cursor: null }),
    );
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Click Send/ }));
    const detail = await screen.findByRole("article", {
      name: "Approval detail",
    });
    expect(detail).toHaveTextContent("computer.click");
    expect(detail).toHaveTextContent("r-1");
  });

  it("shows no detail until an approval is explicitly selected", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      jsonResponse({ items: [approval], next_cursor: null }),
    );
    renderPage();
    await screen.findByRole("button", { name: /Click Send/ });
    expect(
      screen.queryByRole("article", { name: "Approval detail" }),
    ).not.toBeInTheDocument();
  });

  it("approving sends the resolver and refreshes the list", async () => {
    const fetchMock = vi.spyOn(global, "fetch");
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ items: [approval], next_cursor: null }),
    );
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ ...approval, status: "approved", resolver: "patrick" }),
    );
    fetchMock.mockResolvedValue(
      jsonResponse({
        items: [{ ...approval, status: "approved", resolver: "patrick" }],
        next_cursor: null,
      }),
    );

    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Click Send/ }));
    await user.type(screen.getByLabelText("Your name or email"), "patrick");
    await user.click(screen.getByRole("button", { name: "Approve" }));

    const approveCall = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith("/approve"),
    );
    expect(approveCall).toBeDefined();
    expect(JSON.parse(String(approveCall![1]!.body))).toEqual({
      resolver: "patrick",
      resolution_note: undefined,
    });
    expect(
      await screen.findByRole("button", { name: /Click Send — approved/ }),
    ).toBeInTheDocument();
  });

  it("keeps the approval actionable when the API rejects the decision", async () => {
    const fetchMock = vi.spyOn(global, "fetch");
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ items: [approval], next_cursor: null }),
    );
    fetchMock.mockResolvedValue(
      jsonResponse(
        { error: { type: "entity_conflict", message: "already", details: {} } },
        409,
      ),
    );

    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Click Send/ }));
    await user.type(screen.getByLabelText("Your name or email"), "patrick");
    await user.click(screen.getByRole("button", { name: "Approve" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "the approval's status has not changed",
    );
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  it("navigates back to the run", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      jsonResponse({ items: [], next_cursor: null }),
    );
    const { onBackToRun } = renderPage();
    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "Back to run" }));
    expect(onBackToRun).toHaveBeenCalled();
  });

  it("shows an error when the list request fails", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      jsonResponse(
        { error: { type: "run_not_found", message: "nope", details: {} } },
        404,
      ),
    );
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Failed to load approvals.",
    );
  });
});
