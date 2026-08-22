import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { useRoute } from "./use-route";

beforeEach(() => {
  window.history.replaceState({}, "", "/");
});

describe("useRoute", () => {
  it("defaults to the conversation view with no id", () => {
    const { result } = renderHook(() => useRoute());
    expect(result.current[0]).toEqual({ view: "conversation", id: null });
  });

  it("reads an existing ?view=run&id=... URL on mount", () => {
    window.history.replaceState({}, "", "/?view=run&id=r-1");
    const { result } = renderHook(() => useRoute());
    expect(result.current[0]).toEqual({ view: "run", id: "r-1" });
  });

  it("reads an Agent detail route", () => {
    window.history.replaceState({}, "", "/?view=agent&id=a-1");
    const { result } = renderHook(() => useRoute());
    expect(result.current[0]).toEqual({ view: "agent", id: "a-1" });
  });

  it("falls back to conversation for an unknown view", () => {
    window.history.replaceState({}, "", "/?view=nonsense&id=r-1");
    const { result } = renderHook(() => useRoute());
    expect(result.current[0]).toEqual({ view: "conversation", id: null });
  });

  it("navigate() updates both the URL and the returned route", () => {
    const { result } = renderHook(() => useRoute());
    act(() => result.current[1]({ view: "run", id: "r-2" }));
    expect(result.current[0]).toEqual({ view: "run", id: "r-2" });
    expect(window.location.search).toBe("?view=run&id=r-2");
  });

  it("responds to browser back/forward", () => {
    const { result } = renderHook(() => useRoute());
    act(() => result.current[1]({ view: "approvals", id: "r-3" }));
    act(() => {
      window.history.replaceState({}, "", "/?view=tasks");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(result.current[0]).toEqual({ view: "tasks", id: null });
  });
});
