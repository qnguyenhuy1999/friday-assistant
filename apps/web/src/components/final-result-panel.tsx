import type { JsonValue, Run, RunEvent } from "@friday/contracts";
function object(value: JsonValue): Record<string, JsonValue> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, JsonValue>)
    : null;
}
export function FinalResultPanel({
  run,
  events = [],
}: {
  run: Run;
  events?: RunEvent[];
}) {
  if (run.status === "succeeded") {
    const payload = object(
      [...events].reverse().find((event) => event.type === "agent_finished")
        ?.payload ?? null,
    );
    return (
      <div role="status">
        <p>Run succeeded.</p>
        {payload && (
          <>
            <h3>Final answer</h3>
            {typeof payload.summary === "string" && <p>{payload.summary}</p>}
            {payload.details !== undefined && (
              <pre>
                {typeof payload.details === "string"
                  ? payload.details
                  : JSON.stringify(payload.details, null, 2)}
              </pre>
            )}
          </>
        )}
      </div>
    );
  }
  if (run.status === "cancelled")
    return (
      <div role="status">
        <p>Run was cancelled.</p>
      </div>
    );
  if (run.status === "failed")
    return (
      <div role="alert">
        <p>Run failed{run.failure ? `: ${run.failure.message}` : "."}</p>
        {run.failure && (
          <dl>
            <dt>Code</dt>
            <dd>{run.failure.code}</dd>
            <dt>Cause</dt>
            <dd>{run.failure.cause}</dd>
            <dt>Retryable</dt>
            <dd>{run.failure.retryable ? "yes" : "no"}</dd>
          </dl>
        )}
      </div>
    );
  return null;
}
