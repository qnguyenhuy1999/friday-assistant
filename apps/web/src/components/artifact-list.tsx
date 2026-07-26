import type { Artifact } from "@friday/contracts";
export function ArtifactList({ artifacts }: { artifacts: Artifact[] }) {
  return (
    <ul aria-label="Artifacts">
      {artifacts.map((a) => (
        <li key={a.artifact_id}>
          {a.name} ({a.kind}) — {a.location}
          <dl>
            <dt>Media type</dt>
            <dd>{a.media_type}</dd>
            <dt>Size</dt>
            <dd>{a.size ?? "—"}</dd>
            <dt>Checksum</dt>
            <dd>{a.checksum ?? "—"}</dd>
            <dt>Step</dt>
            <dd>{a.step_id ?? "—"}</dd>
            <dt>Created at</dt>
            <dd>{a.created_at}</dd>
            <dt>Metadata</dt>
            <dd>
              <pre>{JSON.stringify(a.metadata, null, 2)}</pre>
            </dd>
          </dl>
        </li>
      ))}
    </ul>
  );
}
