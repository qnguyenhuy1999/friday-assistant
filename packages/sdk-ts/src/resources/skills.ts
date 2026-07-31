import type {
  CreateSkillBody,
  CreateSkillRevisionBody,
  Page,
  Skill,
  SkillRevision,
} from "@friday/contracts";
import {
  validateSkill,
  validateSkillPage,
  validateSkillRevision,
  validateSkillRevisions,
} from "@friday/contracts";
import type { FridayHttpClient } from "../http";

export class SkillsResource {
  constructor(private readonly http: FridayHttpClient) {}
  create(input: CreateSkillBody) {
    return this.http.requestJson<Skill>({
      method: "POST",
      path: "/v1/skills",
      body: input,
      validate: validateSkill,
    });
  }
  list() {
    return this.http.requestJson<Page<Skill>>({
      method: "GET",
      path: "/v1/skills",
      validate: validateSkillPage,
    });
  }
  get(skillId: string) {
    return this.http.requestJson<Skill>({
      method: "GET",
      path: `/v1/skills/${skillId}`,
      validate: validateSkill,
    });
  }
  createRevision(skillId: string, input: CreateSkillRevisionBody) {
    return this.http.requestJson<SkillRevision>({
      method: "POST",
      path: `/v1/skills/${skillId}/revisions`,
      body: input,
      validate: validateSkillRevision,
    });
  }
  listRevisions(skillId: string) {
    return this.http.requestJson<SkillRevision[]>({
      method: "GET",
      path: `/v1/skills/${skillId}/revisions`,
      validate: validateSkillRevisions,
    });
  }
  activateRevision(skillId: string, revisionId: string) {
    return this.http.requestJson<Skill>({
      method: "POST",
      path: `/v1/skills/${skillId}/revisions/${revisionId}/activate`,
      validate: validateSkill,
    });
  }
  disable(skillId: string) {
    return this.http.requestJson<Skill>({
      method: "POST",
      path: `/v1/skills/${skillId}/disable`,
      validate: validateSkill,
    });
  }
  archive(skillId: string) {
    return this.http.requestJson<Skill>({
      method: "POST",
      path: `/v1/skills/${skillId}/archive`,
      validate: validateSkill,
    });
  }
}
