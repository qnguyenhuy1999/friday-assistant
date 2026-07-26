// Compile-time fixtures: these literals are shaped like real `apps/api`
// payloads, so a wire type that drifts from the API fails `just typecheck`.
import type { ApiErrorBody, Failure, JsonValue, Page } from "../../index";

export const jsonValueExample: JsonValue = { a: 1, b: [true, null, "x"] };

export const failureExample: Failure = {
  code: "tool_timeout",
  message: "the shell command exceeded its timeout",
  retryable: true,
  cause: "timeout",
  details: null,
};

export const apiErrorExample: ApiErrorBody = {
  error: { type: "run_not_found", message: "run not found", details: {} },
};

export const pageExample: Page<Failure> = {
  items: [failureExample],
  next_cursor: null,
};
