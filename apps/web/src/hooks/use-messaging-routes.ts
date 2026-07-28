import { useQuery } from "@tanstack/react-query";
import { friday } from "../friday-client";

export function useMessagingRoutes() {
  return useQuery({
    queryKey: ["messaging-routes"],
    queryFn: () => friday.messaging.listRoutes(),
  });
}
