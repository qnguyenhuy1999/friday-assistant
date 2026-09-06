import type { SkillEvidenceSnapshot } from "@friday/contracts";

export type EvidenceKind = "usage" | "feedback" | "manual";

export interface EvidenceEntry {
  id: string;
  kind: EvidenceKind;
  payload: unknown;
}

export function formatTime(value: string | null | undefined) {
  if (value === null || value === undefined) return "Not recorded";
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.valueOf()) ? value : timestamp.toLocaleString();
}

export function formatJson(value: unknown) {
  const formatted = JSON.stringify(value, null, 2);
  return formatted === undefined ? "Unable to format value" : formatted;
}

export function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Unknown server error";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseEvidenceSnapshot(
  snapshot: SkillEvidenceSnapshot,
  skillId: string,
  baseRevisionId: string,
): { entries: EvidenceEntry[] } | { error: string } {
  const evidence = snapshot.evidence as unknown;
  if (!isRecord(evidence) || evidence.version !== 1) {
    return { error: "Evidence snapshot schema could not be verified." };
  }
  if (!Array.isArray(evidence.entries)) {
    return { error: "Evidence snapshot schema could not be verified." };
  }
  const entries: EvidenceEntry[] = [];
  const ids = new Set<string>();
  for (const rawEntry of evidence.entries) {
    if (!isRecord(rawEntry)) {
      return { error: "Evidence snapshot schema could not be verified." };
    }
    const id = rawEntry.id;
    const kind = rawEntry.kind;
    if (
      typeof id !== "string" ||
      id.length === 0 ||
      ids.has(id) ||
      (kind !== "usage" && kind !== "feedback" && kind !== "manual") ||
      !Object.prototype.hasOwnProperty.call(rawEntry, "payload")
    ) {
      return { error: "Evidence snapshot schema could not be verified." };
    }
    const payload = rawEntry.payload;
    if ((kind === "usage" || kind === "feedback") && !isRecord(payload)) {
      return { error: "Evidence snapshot schema could not be verified." };
    }
    if (isRecord(payload)) {
      if (payload.skill_id !== undefined && payload.skill_id !== skillId) {
        return { error: "Evidence snapshot provenance could not be verified." };
      }
      if (
        payload.revision_id !== undefined &&
        payload.revision_id !== baseRevisionId
      ) {
        return { error: "Evidence snapshot provenance could not be verified." };
      }
    }
    ids.add(id);
    entries.push({ id, kind, payload });
  }
  return { entries };
}

export function isKnownClosedProposalStatus(status: string) {
  return (
    status === "promoted" || status === "cancelled" || status === "rejected"
  );
}
