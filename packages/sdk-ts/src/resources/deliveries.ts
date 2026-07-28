import type { FridayHttpClient } from "../http";

/** Safe status projection: delivery bodies and route credentials are absent. */
export interface Delivery {
  id: string;
  source_kind: string;
  source_run_id: string;
  source_schedule_fire_id: string | null;
  route_id: string;
  status: string;
  available_at: string;
  attempt_count: number;
  failure_code: string | null;
  created_at: string;
  updated_at: string;
  delivered_at: string | null;
}

export interface DeliveryPage {
  items: Delivery[];
}

export class DeliveriesResource {
  constructor(private readonly http: FridayHttpClient) {}

  listForRun(runId: string) {
    return this.http.request<DeliveryPage>({
      method: "GET",
      path: `/v1/runs/${runId}/deliveries`,
    });
  }
  get(deliveryId: string) {
    return this.http.request<Delivery>({
      method: "GET",
      path: `/v1/deliveries/${deliveryId}`,
    });
  }
  cancel(deliveryId: string) {
    return this.http.request<Delivery>({
      method: "POST",
      path: `/v1/deliveries/${deliveryId}/cancel`,
    });
  }
}
