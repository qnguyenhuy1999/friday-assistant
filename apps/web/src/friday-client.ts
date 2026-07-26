import { FridayClient } from "@friday/sdk";
export const friday = new FridayClient({
  baseUrl: import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000",
});
