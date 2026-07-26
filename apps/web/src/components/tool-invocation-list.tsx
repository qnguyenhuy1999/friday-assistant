import type { ToolInvocation } from "@friday/contracts";
export function ToolInvocationList({
  invocations,
}: {
  invocations: ToolInvocation[];
}) {
  return (
    <ul aria-label="Tool invocations">
      {invocations.map((i) => (
        <li key={i.invocation_id}>
          {i.tool_name} — {i.status}
          <dl>
            <dt>Requested at</dt>
            <dd>{i.requested_at}</dd>
            <dt>Step</dt>
            <dd>{i.step_id ?? "—"}</dd>
            <dt>Approval</dt>
            <dd>{i.approval_request_id ?? "—"}</dd>
            {i.output_set && (
              <>
                <dt>Output</dt>
                <dd>
                  <pre>{JSON.stringify(i.output, null, 2)}</pre>
                </dd>
              </>
            )}
            {i.failure && (
              <>
                <dt>Failure</dt>
                <dd>
                  {i.failure.code}: {i.failure.message}
                </dd>
              </>
            )}
          </dl>
        </li>
      ))}
    </ul>
  );
}
