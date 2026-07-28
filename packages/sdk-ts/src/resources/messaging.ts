import type { FridayHttpClient } from "../http";

export interface MessagingRoute {
  route_id: string;
  trusted_description: string;
  transport: string;
  enabled: boolean;
}

export interface MessagingRoutePage {
  items: MessagingRoute[];
}

export class MessagingResource {
  constructor(private readonly http: FridayHttpClient) {}

  listRoutes() {
    return this.http.request<MessagingRoutePage>({
      method: "GET",
      path: "/v1/messaging/routes",
    });
  }
}
