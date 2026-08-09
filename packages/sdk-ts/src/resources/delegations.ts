import type { DelegationRequest } from "@friday/contracts";
import { validateDelegationRequest } from "@friday/contracts";
import type { FridayHttpClient } from "../http";
export class DelegationsResource {
  constructor(private readonly http: FridayHttpClient) {}
  get(delegationId: string) {
    return this.http.requestJson<DelegationRequest>({
      method: "GET",
      path: `/v1/delegations/${delegationId}`,
      validate: validateDelegationRequest,
    });
  }
}
