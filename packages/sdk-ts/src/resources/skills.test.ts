import {
  validateSkill,
  validateSkillPage,
  validateSkillRevision,
  validateSkillRevisions,
  validateSkillPromotion,
} from "@friday/contracts";
import { describe, expect, it, vi } from "vitest";
import { FridayHttpClient } from "../http";
import { SkillsResource } from "./skills";

describe("SkillsResource", () => {
  it("maps create, list, get, revision, and lifecycle operations", async () => {
    const requestJson = vi.fn().mockResolvedValue({});
    const skills = new SkillsResource({
      requestJson,
    } as unknown as FridayHttpClient);

    await skills.create({
      key: "research.roundtrip",
      display_name: "Roundtrip",
      description: "desc",
    });
    await skills.list();
    await skills.get("s-1");
    await skills.createRevision("s-1", {
      instructions: "Repair tests",
      source_kind: "operator",
    });
    await skills.listRevisions("s-1");
    await skills.activateRevision("s-1", "r-1");
    await skills.disable("s-1");
    await skills.archive("s-1");

    expect(requestJson).toHaveBeenNthCalledWith(1, {
      method: "POST",
      path: "/v1/skills",
      body: {
        key: "research.roundtrip",
        display_name: "Roundtrip",
        description: "desc",
      },
      validate: validateSkill,
    });
    expect(requestJson).toHaveBeenNthCalledWith(2, {
      method: "GET",
      path: "/v1/skills",
      query: { limit: undefined, cursor: undefined },
      validate: validateSkillPage,
    });
    expect(requestJson).toHaveBeenNthCalledWith(3, {
      method: "GET",
      path: "/v1/skills/s-1",
      validate: validateSkill,
    });
    expect(requestJson).toHaveBeenNthCalledWith(4, {
      method: "POST",
      path: "/v1/skills/s-1/revisions",
      body: { instructions: "Repair tests", source_kind: "operator" },
      validate: validateSkillRevision,
    });
    expect(requestJson).toHaveBeenNthCalledWith(5, {
      method: "GET",
      path: "/v1/skills/s-1/revisions",
      validate: validateSkillRevisions,
    });
    expect(requestJson).toHaveBeenNthCalledWith(6, {
      method: "POST",
      path: "/v1/skills/s-1/revisions/r-1/activate",
      validate: validateSkill,
    });
    expect(requestJson).toHaveBeenNthCalledWith(7, {
      method: "POST",
      path: "/v1/skills/s-1/disable",
      validate: validateSkill,
    });
    expect(requestJson).toHaveBeenNthCalledWith(8, {
      method: "POST",
      path: "/v1/skills/s-1/archive",
      validate: validateSkill,
    });
  });

  it("passes bounded collection and revision pagination parameters", async () => {
    const requestJson = vi
      .fn()
      .mockResolvedValue({ items: [], next_cursor: null });
    const skills = new SkillsResource({
      requestJson,
    } as unknown as FridayHttpClient);

    await skills.list({ limit: 2, cursor: "skills-page-2" });
    await skills.listRevisionsPage("s-1", {
      limit: 2,
      beforeVersion: 5,
    });

    expect(requestJson).toHaveBeenNthCalledWith(1, {
      method: "GET",
      path: "/v1/skills",
      query: { limit: 2, cursor: "skills-page-2" },
      validate: validateSkillPage,
    });
    expect(requestJson).toHaveBeenNthCalledWith(2, {
      method: "GET",
      path: "/v1/skills/s-1/revisions",
      query: { limit: 2, before_version: 5 },
      validate: validateSkillRevisions,
    });
  });

  it("gets an exact revision with the runtime validator", async () => {
    const requestJson = vi.fn().mockResolvedValue({});
    const skills = new SkillsResource({
      requestJson,
    } as unknown as FridayHttpClient);

    await skills.getRevision("s-1", "r-1");

    expect(requestJson).toHaveBeenCalledWith({
      method: "GET",
      path: "/v1/skills/s-1/revisions/r-1",
      validate: validateSkillRevision,
    });
  });

  it("uses contract validation for skill responses", async () => {
    const skills = new SkillsResource(
      new FridayHttpClient({
        baseUrl: "http://api.test",
        fetchImpl: vi
          .fn()
          .mockResolvedValue(
            new Response(JSON.stringify({ id: "missing-required-fields" })),
          ),
      }),
    );

    await expect(skills.get("s-1")).rejects.toThrow(/wire contract/);
  });

  it("rejects malformed Phase 20 promotion responses", async () => {
    const skills = new SkillsResource(
      new FridayHttpClient({
        baseUrl: "http://api.test",
        fetchImpl: vi.fn().mockResolvedValue(
          new Response(
            JSON.stringify({
              id: "promotion-1",
              status: "pending",
            }),
          ),
        ),
      }),
    );

    await expect(skills.getPromotion("promotion-1")).rejects.toThrow(
      /wire contract/,
    );
    expect(validateSkillPromotion).toBeTypeOf("function");
  });
});
