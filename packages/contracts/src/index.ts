export const CONTRACTS_VERSION = "v1" as const;

export const contractsPackageMetadata = {
  name: "@friday/contracts",
  status: "active",
  version: CONTRACTS_VERSION,
} as const;

/**
 * Repo-relative path to a canonical schema file under this version's schema
 * set, e.g. schemaPath("task/task.json") -> "schemas/v1/task/task.json".
 * Generated language bindings must resolve schemas through this helper
 * rather than hardcoding the version segment, so a version bump is a
 * one-line change.
 */
export function schemaPath(relativePath: string): string {
  return `schemas/${CONTRACTS_VERSION}/${relativePath}`;
}
