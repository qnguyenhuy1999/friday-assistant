import type { Run } from "@friday/contracts";
export function FinalResultPanel({ run }: { run: Run }) {
  if (run.status === "succeeded")
    return (
      <div role="status">
        <p>Run succeeded.</p>
      </div>
    );
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
