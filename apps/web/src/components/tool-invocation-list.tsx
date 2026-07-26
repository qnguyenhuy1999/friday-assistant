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
        </li>
      ))}
    </ul>
  );
}
