import Ajv from "ajv";

/**
 * Runtime validation for the versioned HTTP wire format.  The schema is the
 * SDK's compatibility boundary: consumers must never receive a value merely
 * asserted to be a TypeScript interface.
 */
const string = { type: "string" } as const;
const nullableString = { anyOf: [string, { type: "null" }] } as const;
const json = {} as const;
const failure = {
  type: "object",
  additionalProperties: false,
  required: ["code", "message", "retryable", "cause", "details"],
  properties: {
    code: string,
    message: string,
    retryable: { type: "boolean" },
    cause: {
      enum: [
        "validation",
        "tool",
        "runtime",
        "approval",
        "cancelled",
        "timeout",
        "internal",
      ],
    },
    details: json,
  },
} as const;
const entity = (
  required: readonly string[],
  properties: Record<string, object>,
) => ({
  type: "object",
  additionalProperties: false,
  required,
  properties,
});
const task = entity(
  ["id", "title", "description", "status", "created_at", "failure"],
  {
    id: string,
    title: string,
    description: string,
    status: { enum: ["pending", "active", "completed", "failed", "cancelled"] },
    created_at: string,
    failure: { anyOf: [failure, { type: "null" }] },
  },
);
const run = entity(["id", "task_id", "status", "created_at", "failure"], {
  id: string,
  task_id: string,
  status: {
    enum: [
      "queued",
      "running",
      "waiting_for_approval",
      "succeeded",
      "failed",
      "cancelled",
    ],
  },
  created_at: string,
  failure: { anyOf: [failure, { type: "null" }] },
});
const step = entity(["id", "run_id", "name", "position", "status", "failure"], {
  id: string,
  run_id: string,
  name: string,
  position: { type: "integer" },
  status: {
    enum: [
      "pending",
      "running",
      "waiting_for_approval",
      "succeeded",
      "failed",
      "skipped",
      "cancelled",
    ],
  },
  failure: { anyOf: [failure, { type: "null" }] },
});
const approval = entity(
  [
    "approval_id",
    "run_id",
    "step_id",
    "category",
    "summary",
    "reason",
    "requested_action",
    "requested_input",
    "status",
    "requested_at",
    "expires_at",
    "resolved_at",
    "resolution_note",
    "resolver",
    "authorization_fingerprint",
    "consumed_at",
  ],
  {
    approval_id: string,
    run_id: string,
    step_id: nullableString,
    category: {
      enum: [
        "tool_execution",
        "filesystem_write",
        "network_access",
        "computer_use",
        "other",
      ],
    },
    summary: string,
    reason: string,
    requested_action: string,
    requested_input: json,
    status: {
      enum: ["pending", "approved", "rejected", "cancelled", "expired"],
    },
    requested_at: string,
    expires_at: nullableString,
    resolved_at: nullableString,
    resolution_note: nullableString,
    resolver: nullableString,
    authorization_fingerprint: nullableString,
    consumed_at: nullableString,
  },
);
const invocation = entity(
  [
    "invocation_id",
    "run_id",
    "step_id",
    "tool_name",
    "status",
    "requested_at",
    "approval_request_id",
    "output",
    "output_set",
    "failure",
  ],
  {
    invocation_id: string,
    run_id: string,
    step_id: nullableString,
    tool_name: string,
    status: {
      enum: ["requested", "running", "succeeded", "failed", "cancelled"],
    },
    requested_at: string,
    approval_request_id: nullableString,
    output: json,
    output_set: { type: "boolean" },
    failure: { anyOf: [failure, { type: "null" }] },
  },
);
const artifact = entity(
  [
    "artifact_id",
    "run_id",
    "step_id",
    "kind",
    "name",
    "media_type",
    "location",
    "created_at",
    "size",
    "checksum",
    "metadata",
  ],
  {
    artifact_id: string,
    run_id: string,
    step_id: nullableString,
    kind: {
      enum: ["text", "file", "directory", "url", "json", "image", "other"],
    },
    name: string,
    media_type: string,
    location: string,
    created_at: string,
    size: { anyOf: [{ type: "integer" }, { type: "null" }] },
    checksum: nullableString,
    metadata: json,
  },
);
const event = entity(
  [
    "event_id",
    "run_id",
    "step_id",
    "type",
    "sequence",
    "occurred_at",
    "payload",
  ],
  {
    event_id: string,
    run_id: string,
    step_id: nullableString,
    type: {
      enum: [
        "run_created",
        "run_started",
        "run_waiting_for_approval",
        "run_resumed",
        "run_succeeded",
        "run_failed",
        "run_cancelled",
        "step_created",
        "step_started",
        "step_succeeded",
        "step_failed",
        "step_skipped",
        "step_cancelled",
        "approval_requested",
        "approval_resolved",
        "tool_invocation_requested",
        "tool_invocation_started",
        "tool_invocation_succeeded",
        "tool_invocation_failed",
        "tool_invocation_cancelled",
        "artifact_created",
        "agent_finished",
        "memory_context_attached",
        "memory_retrieval_degraded",
        "memory_write_requested",
        "memory_write_committed",
        "memory_write_conflicted",
        "memory_index_marked_stale",
      ],
    },
    sequence: { type: "integer" },
    occurred_at: string,
    payload: json,
  },
);
const page = (item: object) =>
  entity(["items", "next_cursor"], {
    items: { type: "array", items: item },
    next_cursor: nullableString,
  });
const schemas = {
  task,
  run,
  step,
  approval,
  invocation,
  artifact,
  event,
  taskPage: page(task),
  runPage: page(run),
  stepPage: page(step),
  approvalPage: page(approval),
  invocationPage: page(invocation),
  artifactPage: page(artifact),
  eventPage: page(event),
  startRun: entity(["task_id", "run_id"], { task_id: string, run_id: string }),
};
const ajv = new Ajv({ allErrors: true, strict: true });
const validators = Object.fromEntries(
  Object.entries(schemas).map(([name, schema]) => [name, ajv.compile(schema)]),
);
export class WireFormatError extends Error {
  constructor(
    readonly path: string,
    readonly issues: string,
  ) {
    super(
      `Response for ${path} does not match the v1 HTTP wire contract: ${issues}`,
    );
    this.name = "WireFormatError";
  }
}
function schemaName(
  path: string,
  value: unknown,
): keyof typeof schemas | undefined {
  const isPage = !!value && typeof value === "object" && "items" in value;
  if (/\/events(?:\/|$)/.test(path)) return isPage ? "eventPage" : "event";
  if (/\/approvals(?:\/|$)/.test(path))
    return isPage ? "approvalPage" : "approval";
  if (/\/tool-invocations(?:\/|$)/.test(path))
    return isPage ? "invocationPage" : "invocation";
  if (/\/artifacts(?:\/|$)/.test(path))
    return isPage ? "artifactPage" : "artifact";
  if (/\/steps(?:\/|$)/.test(path)) return isPage ? "stepPage" : "step";
  if (/\/tasks\/[^/]+\/runs$/.test(path) && !isPage) return "startRun";
  if (/\/tasks(?:\/|$)/.test(path)) return isPage ? "taskPage" : "task";
  if (/\/runs(?:\/|$)/.test(path)) return isPage ? "runPage" : "run";
  return undefined;
}
export function validateWireResponse(path: string, value: unknown): void {
  const name = schemaName(path, value);
  if (!name) return;
  const validate = validators[name];
  if (!validate) throw new Error(`Missing wire validator: ${name}`);
  if (!validate(value))
    throw new WireFormatError(path, ajv.errorsText(validate.errors));
}
export function validateRunEvent(value: unknown): void {
  const validate = validators.event;
  if (!validate) throw new Error("Missing wire validator: event");
  if (!validate(value))
    throw new WireFormatError("SSE run event", ajv.errorsText(validate.errors));
}
