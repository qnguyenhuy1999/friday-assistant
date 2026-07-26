import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { startTestApiServer, type TestApiServer } from "../../test/api-server";

let server: TestApiServer;
beforeAll(async () => {
  server = await startTestApiServer();
  vi.stubEnv("VITE_API_BASE_URL", server.baseUrl);
  const { EventSource } = await import("eventsource");
  vi.stubGlobal("EventSource", EventSource);
}, 60_000);
afterAll(async () => {
  await server.stop();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
describe("control plane", () => {
  it("creates, approves, and completes a run through the UI", async () => {
    const { FridayClient } = await import("@friday/sdk");
    const { QueryClient, QueryClientProvider } =
      await import("@tanstack/react-query");
    const { App } = await import("../App");
    const friday = new FridayClient({ baseUrl: server.baseUrl });
    const task = await friday.tasks.create({ title: "Ship Phase 14" });
    const { run_id } = await friday.tasks.startRun(task.id);
    await friday.runs.start(run_id);
    await friday.approvals.request(run_id, {
      category: "computer_use",
      summary: "Click Send in Messages",
      reason: "Explicit approval is required",
      requested_action: "computer.click",
      requested_input: { pid: 844, element: { role: "button", label: "Send" } },
    });
    window.history.replaceState({}, "", `/?view=run&id=${run_id}`);
    render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <App />
      </QueryClientProvider>,
    );
    expect(await screen.findByText(/1 pending approval/)).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "View approvals" }));
    await user.click(await screen.findByRole("button", { name: /Click Send/ }));
    // The authorization intent must survive the real wire round-trip intact:
    // action, requested_input as literal JSON, and the originating run.
    const detail = await screen.findByRole("article", {
      name: "Approval detail",
    });
    expect(detail).toHaveTextContent("computer.click");
    expect(detail).toHaveTextContent('"label": "Send"');
    expect(detail).toHaveTextContent('"pid": 844');
    expect(detail).toHaveTextContent(run_id);
    await user.type(screen.getByLabelText("Your name or email"), "patrick");
    await user.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(async () =>
      expect((await friday.approvals.listForRun(run_id)).items[0]?.status).toBe(
        "approved",
      ),
    );
    await user.click(screen.getByRole("button", { name: "Back to run" }));
    // Wait for Run Detail to remount (and its SSE subscription to open) before
    // completing: the run_succeeded frame delivered over the live stream is the
    // refresh path under test, not the 5s useRun poll that would mask it.
    await screen.findByText(`Run ${run_id}`);
    await friday.runs.complete(run_id);
    expect(
      await screen.findByRole("status", undefined, { timeout: 15_000 }),
    ).toHaveTextContent("Run succeeded.");
  }, 60_000);
});
