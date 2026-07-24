# Phase 11 — Brain Runtime, Approval Interception & Safe Tool Execution

Phase 11 connects Phase 10's durable worker coordination to a real agent
runtime. The architectural rule is absolute:

> Claude CLI proposes actions. Friday validates, authorizes, executes,
> persists, and fences actions.

Claude does not execute tools directly. Friday owns every side effect.
Phase 11 is **not** an OS sandbox: it provides policy enforcement and
workspace confinement. Memory begins in Phase 12; computer use in Phase 13.

## Brain-only Claude CLI

`friday.infrastructure.brain.claude_cli.ClaudeCliBrainRuntime` invokes the
locally authenticated Claude CLI in a **process-level** brain-only mode —
never merely a prompt instruction:

| Flag | Guarantee |
| --- | --- |
| `--tools ""` | every built-in tool disabled |
| `--strict-mcp-config` (no config) | no MCP servers |
| `--safe-mode` | no hooks, plugins, CLAUDE.md, custom commands |
| `--no-session-persistence` | nothing written to CLI session storage |
| `-p --output-format json` | one machine-readable result envelope |

The prompt travels via **stdin** (never argv — invisible to `ps`). The
subprocess environment is built from an explicit allowlist
(`ENVIRONMENT_ALLOWLIST`: HOME, PATH, USER, LOGNAME, SHELL, TMPDIR, LANG,
LC_ALL, LC_CTYPE, TERM). `ANTHROPIC_API_KEY` and nested `CLAUDE_CODE_*`
variables are deliberately dropped — authentication is the on-disk
claude.ai subscription only; no API key or Anthropic SDK exists anywhere
(enforced by `tests/architecture/test_phase11_boundaries.py` and the
dependency policy).

Timeouts SIGKILL the whole process group and drain pipes; stdout and the
model response are byte-capped; stderr is never logged or echoed into
errors (size only). One bounded repair attempt is made for a schema-invalid
response, then `BrainResponseInvalid`. Startup calls
`verify_brain_only_support()` — a CLI that does not advertise every
required flag fails the worker before any claim (fail-closed).

## Action contract

`packages/contracts/schemas/v1/runtime/brain_action.json` is the canonical
envelope: a strict `oneOf` of `finish`, `fail`, `yield`, `invoke_tool`
(version 1, bounded strings, `additionalProperties: false`).
`friday.application.runtime_actions.parse_brain_action` mirrors it in
stdlib code (jsonschema is dev-only) and rejects unknown actions, unknown
fields, wrong versions, bool-as-int, and oversized values. There is no
action that touches Run/Step/Approval/ToolInvocation status — the model
cannot set lifecycle state.

## Deterministic context

`runtime_context.build_runtime_context` renders one Run's durable state
(objective, run/steps, approvals, tool invocations with bounded outputs,
artifacts, tool manifest, in-claim turn notes, recent events) under an
explicit character budget. Ordering and truncation are deterministic;
omissions are announced to the brain (`[N older ... omitted]`). Tool
results reach the brain only through this document — turn N+1 reads turn
N's invocation output. No environment, secrets, unrelated runs, or hidden
summarization (semantic compression is Phase 12).

## AgentRunProcessor

`agent_run_processor.AgentRunProcessor` implements Phase 10's
`RunProcessor`. Per claim it runs up to `max_turns_per_claim` turns and
`max_tool_calls_per_claim` tool calls, then yields for a fresh lease.
Around **every** external call it checks `ClaimContext.is_lease_lost()`
plus a durable `VerifyRunClaim`; on loss it returns `yielded(now)` — the
loop discards stale outcomes and every requeue path is fenced. No Unit of
Work is ever open while the CLI or a tool runs.

Outcome mapping: `finish` → `succeeded()` (only when steps/invocations are
terminal; otherwise the rejection becomes a turn note), `fail` →
`agent_reported_failure` (non-retryable), `yield` → bounded
`yielded(available_at)`, brain transport errors → stable retryable codes.

## ToolGateway and workspace confinement

`WorkspaceToolGateway` owns the registry and risk matrix:

| Tool | Risk | Approval |
| --- | --- | --- |
| `workspace.list` | read-only | none |
| `workspace.read_text` | read-only | none |
| `workspace.write_text` | mutating (`filesystem_write`) | **required** |
| `process.run` | high (`tool_execution`) | **required** |

All paths pass `workspace_paths.resolve_workspace_path`: absolute paths,
`..`, NUL, and symlinks resolving outside the root are rejected by
resolved-path containment (never string prefixes). `process.run` accepts
argv lists only — no shell, no env injection, workspace-confined cwd,
process-group kill on timeout, byte-capped stdout/stderr. Writes are
atomic (`os.replace`) and produce `ArtifactCandidate`s with SHA-256
checksums, recorded as Artifacts with `artifact_created` events. Known
TOCTOU limitation: a symlink introduced between validation and the file
operation is not defended against — documented, not sandbox-grade.

## Approval interception and exact binding

A protected action executes only under an approval whose
`authorization_fingerprint` equals
`sha256(version \n run_id \n step_id \n tool \n canonical_input_json)`
(`tool_authorization.compute_authorization_fingerprint`, stdlib hashlib).
Any change to run, step, tool, or input produces a different fingerprint.

Authorization matrix: **only `APPROVED` + exact fingerprint + never
consumed** authorizes. Pending, rejected, cancelled, and expired approvals
never authorize; `RunStatus.RUNNING` never implies authorization.
`ApprovalRequest.consume()` (migration `0006`: `authorization_fingerprint`,
`consumed_at`) makes each grant one-shot. `RequestToolApproval` creates
approvals claim-aware: inactive claim ⇒ `ClaimLost`, nothing persisted.
A denied approval simply appears in the next context; the brain chooses
another action, yields, finishes, or fails — Friday does not auto-fail.

## ToolInvocation lifecycle and at-least-once semantics

`claim_aware_tool_execution.ExecuteToolAction`:

```text
Txn A: verify claim → authorize (fingerprint) → consume approval →
       ToolInvocation requested→running → commit
                    │
       gateway.execute(...)            # no transaction open
                    │
Txn B: verify claim → succeed/fail invocation → record artifacts → commit
```

The ToolInvocation ID is allocated before execution and is the idempotency
key. Claim loss between Txn A and Txn B leaves the invocation RUNNING with
its approval consumed — the side effect may or may not have happened.
**Replay policy** for protected actions: a consumed approval's prior
invocation is authoritative — succeeded output is reused, terminal failure
surfaced, and a still-RUNNING prior raises `ToolExecutionAmbiguous`;
Friday never blindly re-executes a non-idempotent action.

## Worker composition and preflight

`apps/worker/app.create_worker(settings, runtime)` verifies the CLI and
workspace **before** constructing anything; `apps/worker/main.build()` is
lazy so a bad environment can never claim a Run. `RuntimeSettings`
(`FRIDAY_WORKER_WORKSPACE_ROOT` required; `FRIDAY_CLAUDE_*`,
`FRIDAY_RUNTIME_*`, `FRIDAY_TOOL_*` bounded) carries no secret.

`just worker-check` runs the preflight (`apps/worker/preflight.py`):
database connectivity, Alembic head, Claude version + brain-only flags,
workspace accessibility — without claiming or executing any Run.

The real-Claude smoke test is manual and never part of CI:

```bash
FRIDAY_CLAUDE_SMOKE=1 uv run pytest tests/infrastructure/test_claude_cli_smoke.py -q
```

## Explicit non-goals (deferred)

Obsidian/Graphify/semantic memory → Phase 12; browser and native
mouse/keyboard computer use → Phase 13; frontend and TypeScript SDK →
Phase 14; production observability, sandboxing, deployment hardening →
Phase 15.
