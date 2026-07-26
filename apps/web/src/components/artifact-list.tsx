import type { Artifact } from "@friday/contracts";
export function ArtifactList({ artifacts }: { artifacts: Artifact[] }) {
  return (
    <ul aria-label="Artifacts">
      {artifacts.map((a) => (
        <li key={a.artifact_id}>
          {a.name} ({a.kind}) — {a.location}
        </li>
      ))}
    </ul>
  );
}
