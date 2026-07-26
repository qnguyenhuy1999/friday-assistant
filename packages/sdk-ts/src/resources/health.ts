import type { FridayHttpClient } from "../http";
export interface HealthStatus {
  status: string;
}
export class HealthResource {
  constructor(private readonly http: FridayHttpClient) {}
  get() {
    return this.http.request<HealthStatus>({ method: "GET", path: "/health" });
  }
}
