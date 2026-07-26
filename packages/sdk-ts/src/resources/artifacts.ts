import type { Artifact, Page, RecordArtifactBody } from "@friday/contracts";
import type { FridayHttpClient } from "../http";
export interface ListArtifactsParams {
  limit?: number;
  cursor?: string;
}
export class ArtifactsResource {
  constructor(private readonly http: FridayHttpClient) {}
  record(runId: string, body: RecordArtifactBody) {
    return this.http.request<Artifact>({
      method: "POST",
      path: `/v1/runs/${runId}/artifacts`,
      body,
    });
  }
  get(id: string) {
    return this.http.request<Artifact>({
      method: "GET",
      path: `/v1/artifacts/${id}`,
    });
  }
  listForRun(runId: string, p: ListArtifactsParams = {}) {
    return this.http.request<Page<Artifact>>({
      method: "GET",
      path: `/v1/runs/${runId}/artifacts`,
      query: { limit: p.limit, cursor: p.cursor },
    });
  }
}
