/** Mirrors JSON values used by the HTTP API's unstructured fields. */
export type JsonValue =
  null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
