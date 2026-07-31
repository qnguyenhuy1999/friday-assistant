/** Mirrors `apps/api/schemas/skills.py`. */
export type SkillStatus = "active" | "disabled" | "archived";
export type SkillRevisionSourceKind = "operator" | "imported";
export interface CreateSkillBody {
  key: string;
  display_name: string;
  description?: string;
}
export interface CreateSkillRevisionBody {
  instructions: string;
  source_kind: SkillRevisionSourceKind;
}
