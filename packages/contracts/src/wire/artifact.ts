import type { JsonValue } from "./json-value";
/** Mirrors `apps/api/schemas/artifacts.py`. */
export type ArtifactKind =
  "text" | "file" | "directory" | "url" | "json" | "image" | "other";
export interface Artifact {
  artifact_id: string;
  run_id: string;
  step_id: string | null;
  kind: ArtifactKind;
  name: string;
  media_type: string;
  location: string;
  created_at: string;
  size: number | null;
  checksum: string | null;
  metadata: JsonValue;
}
export interface RecordArtifactBody {
  kind: ArtifactKind;
  name: string;
  media_type: string;
  location: string;
  step_id?: string;
  size?: number;
  checksum?: string;
  metadata?: JsonValue;
  artifact_id?: string;
}
