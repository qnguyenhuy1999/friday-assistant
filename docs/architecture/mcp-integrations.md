# Friday-owned MCP Integrations

Phase 18 lets a Run call explicitly allow-listed tools on external MCP servers.
MCP is a **transport that sits behind** Friday's existing authority model, never
a second way to reach one.

## Authority boundary

```text
Claude
  -> InvokeToolAction                (a proposal, nothing more)
  -> ToolCall
  -> CompositeToolGateway            (policy-free name routing)
  -> McpToolGateway.assess()         (pure, local, no network)
  -> ExecuteToolAction               (claim fence -> approval -> ToolInvocation)
  -> McpToolGateway.execute()
  -> McpStdioClient                  (tools/call)
  -> one allow-listed remote tool
```

Three paths do not exist and are structurally prevented:

- **Claude CLI to MCP.** The brain runtime is spawned brain-only and verified as
  such at startup; `tests/architecture/test_phase18_boundaries.py` forbids
  `friday.infrastructure.brain.claude_cli` from importing the MCP package.
- **Claude to an arbitrary server.** Every reachable operation is a named local
  tool in a frozen registry built at worker construction.
- **A generic `mcp.call(server, tool, args)` dispatcher.** There is no tool
  whose *input* selects its target. `test_phase18_boundaries.py` asserts no
  registered tool name is `mcp.call`.

## Server trust boundary

> Allowing an MCP stdio server grants that executable the OS privileges of the
> Friday worker process. Friday controls which MCP capabilities are exposed to
> Claude, but Phase 18 does not sandbox third-party server code.

Consequences an operator must accept before enabling a server:

- Server installation and provenance are the operator's responsibility. Friday
  never installs, updates, or fetches an MCP package, and never does so as part
  of a Run.
- The executable is spawned with an argv list and never through a shell. Shell
  interpreters (`sh`, `bash`, `zsh`, `dash`, `ksh`, `fish`, `csh`, `tcsh`,
  `cmd`, `cmd.exe`, `powershell`, `powershell.exe`, `pwsh`, `pwsh.exe`) are
  rejected as the executable at configuration time.
- The child receives only the environment variables named in `env_from`, plus a
  minimal base allowlist. It does not inherit the worker's environment.

## Threat model

| Threat | Control |
| --- | --- |
| Server advertises a dangerous tool (`delete_repo`) | Discovery grants no authority. Only configured bindings survive. |
| Server renames a tool to shadow another | Local name is operator-chosen; remote name is matched exactly; duplicate remote names fail the server closed. |
| Server tool description carries prompt injection | Remote descriptions are never read. `ToolDescriptor.description` is always the Friday-owned `trusted_description`. |
| Server returns a pathological schema (recursive, 10 MB, 10k properties) | `normalize_input_schema` bounds bytes, depth, properties, and enum size, and rejects `$ref`/`$defs`/`definitions`. |
| Server returns unbounded or binary output | `normalize_call_result` bounds items, text length, depth, and total bytes; non-text blocks contribute only their `type` name. |
| Server error text quotes a token | Nothing from a remote message is forwarded. Every failure is a constant message keyed off a stable code. |
| Binding silently changes under a pending approval | The approval fingerprint covers the binding fingerprint; a changed binding produces a different fingerprint and the old approval no longer authorizes. |
| Stale worker completes a mutation | Unchanged Phase 8/11 claim fencing. No MCP-specific execution path exists. |
| Server hangs | Separate connect and call timeouts, both bounded; the call timeout is `min(run remaining budget, server call timeout)`. |
| Orphan child process | `McpToolGateway.close()` is called from `Worker.close()`; each client terminates then kills its child within a bounded grace period. |

## Tool binding identity

A binding is frozen at worker construction and identified by a SHA-256
`binding_fingerprint` over:

```text
binding fingerprint version
server_id
local tool name
remote tool name
transport identity      (sha256 of transport + argv + sorted env_from *names*)
schema identity         (sha256 of the canonical normalized input schema)
risk policy             ("ro"|"rw" : approval_required : approval_category)
```

`transport identity` is a hash, so no argv path text is ever persisted or shown.
`env_from` contributes variable *names* only; resolved values are never hashed,
logged, or stored.

The gateway reports `authorization_scope = "mcp:<binding_fingerprint>"`, and
`compute_authorization_fingerprint` folds that scope into the exact-action
fingerprint. Fingerprint version is bumped to **2**, so every approval created
before Phase 18 fails closed and needs reapproval.

## Discovery policy

Discovery happens once, at gateway construction, outside any transaction.

- Authority is `operator allow-list` intersect `tools/list`. Never `tools/list`
  alone.
- A discovered tool with no binding is ignored and counted as a diagnostic.
- A configured binding with no matching remote tool is *unavailable*: it does
  not enter the manifest, and calling it fails `mcp_unavailable`.
- Duplicate remote tool names, or a `tools/list` exceeding its bounds, fail that
  server closed — every one of its bindings becomes unavailable.
- Unsafe configuration, duplicate local names, normalization collisions, and an
  invalid risk policy **fail worker startup**. They are operator errors, not
  runtime conditions.
- A server being offline does **not** widen authority and does **not** reuse a
  stale snapshot. That server reports unavailable; workspace and computer tools
  keep working.
- The registry is a process-lifetime snapshot. Dynamic authority change is out
  of scope.

## Transport scope

Implemented: **stdio only.** `McpClient` in `client.py` is the seam a Streamable
HTTP transport would implement later without touching `McpToolGateway`.

Not implemented in Phase 18: legacy SSE, OAuth flows, sampling/`createMessage`,
elicitation, MCP prompts, MCP resources, MCP Tasks, MCP Apps, and any
server-initiated model or tool authority. Server-initiated requests arriving on
the stdio channel are ignored, never answered.

The protocol version is negotiated, not asserted: the client offers its
preferred version from `SUPPORTED_PROTOCOL_VERSIONS` and accepts whichever
member of that set the server returns in `initialize`. Anything else is a
protocol error.

## Failure and replay semantics

Stable failure codes, all with constant messages:

```text
mcp_unavailable        server not connected, or binding not available
mcp_connect_timeout    initialize did not complete in the connect budget
mcp_call_timeout       tools/call did not complete in the effective budget
mcp_protocol_error     malformed framing, bad handshake, unusable tool list
mcp_invalid_output     result exceeded a configured bound or was unparseable
mcp_remote_error       the server reported isError, or a JSON-RPC error
```

Retryability follows the binding, not the error: a read-only binding may retry a
transport failure; a **mutating binding is never retryable**, because a call that
timed out may already have landed.

Replay is the unchanged Phase 11 policy. A protected action whose fingerprint
matches an already-consumed approval reuses the prior durable outcome; a prior
invocation still `RUNNING` raises `ToolExecutionAmbiguous`. Friday never
automatically re-executes a mutating MCP operation.

No database transaction is open while connecting, discovering, or calling.
Discovery happens at construction, before any Run can be claimed;
`tools/call` happens between `ExecuteToolAction`'s Txn A and Txn B.

## Secret handling

- Configuration stores variable *names* (`env_from`), never values. A literal
  secret in the config file is a startup error.
- Only named variables plus a minimal base allowlist reach the child.
- Resolved values are never logged, never hashed into a fingerprint, never
  placed in `ToolProvenance`, and never included in a `Failure`.
- `apps/worker/operational_logging.py` uses a field allowlist, so an MCP log
  record can only ever carry the fields named there.
- The child's stderr is drained and discarded, never captured.

## Enabling an integration

MCP is disabled by default. To opt in, set `FRIDAY_MCP_ENABLED=true` and point
`FRIDAY_MCP_CONFIG_PATH` at an operator-owned JSON configuration file. Start
from [mcp.servers.example.json](../../mcp.servers.example.json); every example
server is disabled, so copying it verbatim spawns nothing.

Each binding is the complete allow-list: `local_name` is Friday's tool name,
`remote_tool_name` is the exact server operation, and `trusted_description` is
the only prose Claude receives. `read_only` and `approval_required` are
Friday-owned policy; a mutating binding must require approval.

## Secrets and installation

Install MCP servers yourself from sources you trust. Friday never installs,
updates, or fetches one as part of a Run. An allowed stdio executable has the
worker's OS privileges and is not sandboxed.

`env_from` accepts environment-variable names only, never values. The child
receives those names plus a minimal OS allowlist; it never inherits the worker's
environment wholesale. Keep real `mcp.servers.json` files out of version
control; the repository `.gitignore` covers that filename.

## Verified fixture proof

The test fixture at `tests/infrastructure/mcp_fixture_server.py` speaks MCP over
real stdio pipes. It provides a read-only `fixture.read` and approval-protected
`fixture.write`, allowing tests to prove the same authority path used in
production without live credentials or network access.
