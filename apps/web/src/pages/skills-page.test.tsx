import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SkillsPage } from "./skills-page";

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const skill = {
  id: "s-1",
  key: "research.roundtrip",
  display_name: "Research roundtrip",
  description: "Keeps research grounded.",
  status: "active",
  active_revision_id: "sr-1",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
};

function renderPage(onViewSkill = vi.fn()) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <SkillsPage onViewSkill={onViewSkill} />
    </QueryClientProvider>,
  );
  return { onViewSkill };
}

describe("SkillsPage", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the registry and opens the exact Skill", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      response({ items: [skill], next_cursor: null }),
    );
    const { onViewSkill } = renderPage();
    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "Research roundtrip" }));

    expect(screen.getByLabelText("Skill registry")).toHaveTextContent(
      "research.roundtrip",
    );
    expect(screen.getByLabelText("Skill registry")).toHaveTextContent("active");
    expect(screen.getByLabelText("Skill registry")).toHaveTextContent("sr-1");
    expect(onViewSkill).toHaveBeenCalledWith("s-1");
  });

  it("renders loading-safe empty and error states", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      response({ items: [], next_cursor: null }),
    );
    renderPage();
    expect(
      await screen.findByText(
        "No Skills yet. Create one to begin its immutable revision history.",
      ),
    ).toBeInTheDocument();

    vi.restoreAllMocks();
    vi.spyOn(global, "fetch").mockResolvedValue(
      response({ error: { type: "unavailable", message: "no" } }, 503),
    );
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Failed to load Skills.",
    );
  });

  it("creates a Skill with the exact body and navigates to it", async () => {
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(response({ items: [], next_cursor: null }))
      .mockResolvedValueOnce(response(skill, 201));
    const { onViewSkill } = renderPage();
    const user = userEvent.setup();
    await screen.findByText(/No Skills yet/);
    await user.type(screen.getByLabelText("Key"), " research.roundtrip ");
    await user.type(
      screen.getByLabelText("Display name"),
      " Research roundtrip ",
    );
    await user.type(
      screen.getByLabelText("Description"),
      " Keeps research grounded. ",
    );
    await user.click(screen.getByRole("button", { name: "Create Skill" }));

    expect(onViewSkill).toHaveBeenCalledWith("s-1");
    const request = fetchMock.mock.calls[1];
    expect(request).toBeDefined();
    expect(JSON.parse(String((request?.[1] as RequestInit).body))).toEqual({
      key: "research.roundtrip",
      display_name: "Research roundtrip",
      description: "Keeps research grounded.",
    });
  });

  it("loads additional Skill pages with the opaque cursor", async () => {
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(
        response({ items: [skill], next_cursor: "skills-page-2" }),
      )
      .mockResolvedValueOnce(
        response({
          items: [{ ...skill, id: "s-2", display_name: "Review" }],
          next_cursor: null,
        }),
      );
    renderPage();
    await screen.findByRole("button", { name: "Research roundtrip" });
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Load more Skills" }));
    expect(
      await screen.findByRole("button", { name: "Review" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain(
      "cursor=skills-page-2",
    );
  });

  it("rejects an obviously invalid machine key before the API call", async () => {
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValue(response({ items: [], next_cursor: null }));
    renderPage();
    const user = userEvent.setup();
    await screen.findByText(/No Skills yet/);
    await user.type(screen.getByLabelText("Key"), "Not A Key");
    await user.type(screen.getByLabelText("Display name"), "Invalid");
    await user.click(screen.getByRole("button", { name: "Create Skill" }));
    expect(
      await screen.findByText(
        "Skill key must be lowercase and use dot or hyphen separators.",
      ),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
