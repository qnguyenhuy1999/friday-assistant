import type { Artifact, Page, RecordArtifactBody } from "@friday/contracts";
import { validateArtifact, validateArtifactPage } from "@friday/contracts";
import type { FridayHttpClient } from "../http";
export interface ListArtifactsParams {
  limit?: number;
  cursor?: string;
}
export class ArtifactsResource {
  constructor(private readonly http: FridayHttpClient) {}
  record(runId: string, body: RecordArtifactBody) {
    return this.http.requestJson<Artifact>({
      method: "POST",
      path: `/v1/runs/${runId}/artifacts`,
      body,
      validate: validateArtifact,
    });
  }
  get(id: string) {
    return this.http.requestJson<Artifact>({
      method: "GET",
      path: `/v1/artifacts/${id}`,
      validate: validateArtifact,
    });
  }
  listForRun(runId: string, p: ListArtifactsParams = {}) {
    return this.http.requestJson<Page<Artifact>>({
      method: "GET",
      path: `/v1/runs/${runId}/artifacts`,
      query: { limit: p.limit, cursor: p.cursor },
      validate: validateArtifactPage,
    });
  }
}
