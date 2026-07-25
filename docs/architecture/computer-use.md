# Computer Use (Phase 13)

Friday can inspect a desktop, capture a window, and — with human approval —
move a pointer, click, scroll, type bounded text, press allowed keys, execute
allowed hotkeys, and focus a window.

Two statements govern everything below.

> **Claude does not control the computer directly.** Claude proposes an action.
> Friday validates it, authorizes it against an exact human approval, fences it
> against a live capture and a durable worker claim, executes it, and persists
> the outcome.
>
> **cua-driver is a transport behind Friday's `ComputerToolGateway`, not a
> Claude tool.** Claude cannot see, name, or reach that MCP server. The only
> caller is the gateway, and the only operations invoked are the ten methods on
> the `ComputerDriver` port.

**Computer use is opt-in and defaults to OFF.**

## Execution path

There is exactly one path from a proposed action to a desktop side effect.
Every approval check, claim fence, and snapshot fence sits on it.

```text
Claude (brain-only, proposes a ToolCall)
  ↓
AgentRunProcessor
  ↓
ExecuteToolAction
  ↓ risk assessment (gateway policy)
  ↓ exact approval fingerprint
  ↓
Txn A: verify claim → authorize → consume approval
       → persist ToolInvocation RUNNING → commit
  ↓
fresh durable claim check          ← the only gate before the side effect
  ↓
CompositeToolGateway  (policy-free router)
  ↓
ComputerToolGateway   (all computer-use policy lives here)
  ↓ snapshot fence, target resolution, text/hotkey screening
  ↓
ComputerDriver (Protocol)
  ↓
CuaDriverComputerDriver
  ↓
MCP stdio transport
  ↓
cua-driver process → desktop
  ↓
Txn B: verify claim → persist result/failure/artifacts → commit
```

No transaction is open while the driver runs. There is no second computer-use
state machine: `ExecuteToolAction` treats a click exactly like a workspace
write, and the architecture tests assert it never learns otherwise.

## Tool manifest

Eleven tools, registered only when computer use is enabled. All are
`ApprovalCategory.COMPUTER_USE`.

| Tool | Read-only | Approval |
| --- | --- | --- |
| `computer.active_window` | yes | no |
| `computer.capture` | yes | no |
| `computer.pointer_position` | yes | no |
| `computer.window_list` | yes | no |
| `computer.click` | no | **required** |
| `computer.focus_window` | no | **required** |
| `computer.hotkey` | no | **required** |
| `computer.pointer_move` | no | **required** |
| `computer.press_key` | no | **required** |
| `computer.scroll` | no | **required** |
| `computer.type_text` | no | **required** |

Observation is deliberately approval-free. If looking required a human in the
loop, Claude would be pushed toward proposing blind clicks at guessed
coordinates — cheap observation is what makes "cite a live capture" an
enforceable requirement rather than an obstacle.

The inverse is structural: `ComputerToolPolicy` **cannot be constructed** for a
mutating tool that does not require approval, and the gateway refuses to
register a tool with no policy row (or a policy row with no handler). A new
desktop capability cannot become reachable without passing through
`friday/infrastructure/computer/policy.py` in review.

## Snapshot fencing

Every mutating tool must cite `{snapshot_id, window_id}` naming a live
`computer.capture` result. Pointer tools additionally supply **either**
`element` **or** `x` + `y` — never both, never neither.

```json
{"snapshot_id": "cs_…", "window_id": "win-mail", "element": 14}
{"snapshot_id": "cs_…", "window_id": "win-mail", "x": 300, "y": 220}
```

The gateway fails closed when the snapshot is unknown, expired, or from another
run; when `window_id` disagrees with the captured window; when the element is
not in that snapshot; when a coordinate falls outside the captured window
bounds; or when the target shape is ambiguous. In every case **no driver call
happens** — the tests assert `driver.mutating_calls == ()`, which is the actual
safety property.

`element_id` is meaningful **only inside its snapshot**. `14` is not a
desktop-wide handle, and resolving it globally is the easiest mistake available
here. Resolution happens in the gateway (`targets.py`), never in the driver: a
driver that did its own snapshot lookup would be a second fence implementation,
and the two would eventually disagree.

Snapshots are transient in-process state, not a domain entity — no migration,
no repository. Losing them on restart is correct: a worker that just restarted
cannot vouch for a pre-restart observation of a desktop.

Registry bounds: TTL (checked on lookup, not only on eviction), total count,
per-run count, and run scoping. A capture from the future also fails closed, so
clock skew cannot grant an unbounded lifetime.

## Approval binding

Computer use reuses `compute_authorization_fingerprint()` unchanged — there is
no computer-specific approval hash. The fingerprint covers the run, the step,
the tool name, and the canonical JSON of the entire `tool_input`, so an approval
for one action never authorizes a changed one:

different snapshot · different window · different element · different
coordinate (one pixel is enough) · different button · different click count ·
different text · different key · different modifiers.

Approvals remain one-shot. A replayed identical action reuses the recorded
outcome instead of re-executing.

## Claim fencing and ambiguity

Desktop mutations are **not idempotent**, which drives two rules:

- **Claim lost before the driver call** → the driver is never called; nothing is
  persisted; the approval is not consumed.
- **Claim lost after the side effect but before Txn B** → Friday must not record
  that outcome as an owned success. The `ToolInvocation` stays `RUNNING` and
  ambiguous, its approval consumed. A later claim finding that state raises
  `ToolExecutionAmbiguous` and **never** replays.

Every computer-use failure is reported `retryable=False`, including timeouts. A
timed-out desktop action may already have landed, and a second click is not
free. The gateway's cheap `cancellation_requested()` check is defence in depth,
not a replacement for the durable claim check.

## Screenshot artifacts

Screenshots go through Friday's existing artifact flow — `ArtifactKind.IMAGE`,
`ArtifactCandidate`, Txn B. There is no parallel artifact system.

```text
.friday/artifacts/computer/<invocation-id>/<snapshot-id>.png
```

Keyed by invocation so a later capture can never overwrite the image an earlier
artifact row points at. `.friday/` is git-ignored.

- **Image bytes never enter JSON.** No base64, no data URI, no absolute path.
  Tool output carries `snapshot_id`, a workspace-relative `artifact` location,
  `media_type`, `width`, `height`, `size`, and `checksum`.
- Validation happens **before** the file is created: configured byte ceiling,
  allowed media type (`image/png` only), dimension ceiling, and a PNG
  magic-number check — a driver's declared media type is a claim, not evidence.
- Publication is atomic (temp file + `os.replace`), and SHA-256 checksum and
  size are recorded.
- The adapter bounds the base64 string *before* decoding, so an oversized
  capture is refused without being materialized in memory.

## Keyboard safety

- **Text** is bounded (`FRIDAY_COMPUTER_MAX_TYPE_CHARS`), rejects empty input,
  NUL, and control characters other than tab and newline, and is screened for
  credential shapes by the scanner shared with curated memory writes
  (`friday.application.secret_shapes`).
- **Keys** come from a closed allowlist: the named set (`enter`, `tab`,
  `escape`, `space`, `backspace`, `delete`, arrows, `home`, `end`, `page_up`,
  `page_down`) plus single `[a-z0-9]` characters. **No raw keycodes** — the
  field does not exist and an integer `key` is refused.
- **Hotkeys** allow `meta`, `ctrl`, `alt`, `shift`, canonically ordered with
  duplicates rejected. Normalization happens before comparison, so the
  deny-list cannot be walked around by reordering a list.

The deny-list covers session-level destruction (force quit, log out, lock, shut
down, restart, kill session). It is **not a claim of completeness** — a platform
can always bind something dangerous somewhere — which is why every hotkey needs
approval regardless.

Secret rejection is **defence in depth, not a guarantee**. Friday's real
position is upstream: it never deliberately puts a credential where Claude could
propose typing it, and Phase 13 adds no secret-retrieval path.

## Prompt-injection boundary

Window titles and element labels are attacker-influenceable. A window may
literally title itself `IGNORE ALL PREVIOUS INSTRUCTIONS` or
`{"action":"finish"}`.

Observed text is control-stripped, whitespace-collapsed, and length-bounded by
the value objects, so it cannot forge a section boundary in the context
document. Capture output labels it untrusted explicitly, and the brain system
prompt states that everything inside a tool output is untrusted data that can
never grant permission, satisfy an approval, or relax a limit.

Prompting is the *weakest* of these defences. The load-bearing ones are
structural: on-screen text has no influence over approval, snapshot fencing, or
the allowlists.

## cua-driver integration

`CuaDriverComputerDriver` implements the `ComputerDriver` port over a minimal
MCP stdio client (`mcp_stdio.py`) — newline-delimited JSON-RPC 2.0 to one
locally spawned process. Written rather than imported: `initialize`,
`tools/list`, and `tools/call` are all that is needed, and an agent framework
would add a tool-dispatch loop and plugin registry Friday must not have. No
Anthropic or OpenAI SDK; authentication remains the local Claude CLI
subscription.

- **Startup health check** is deterministic and *total*: `tools/list` must
  advertise every one of the ten mapped tools. A driver that can capture but not
  click is reported unavailable immediately, rather than failing at the first
  click.
- **Tool names** are a fixed, reviewable default table (`CuaToolNames`), one slot
  per driver method. Overridable for a differently-named build, but only those
  ten slots exist, so no override widens what Friday can invoke. There is
  deliberately **no** generic `call(action, payload)` above the transport.
- **Timeouts** are per request; a timeout raises `ComputerDriverTimeout` and is
  reported non-retryable.
- **Reads are bounded.** A response line that does not terminate within the
  ceiling is treated as a dead connection, not buffered further.
- **Stderr is drained and discarded.** Without the drain, a chatty driver fills
  its pipe and blocks forever, looking exactly like a timeout. Its diagnostics
  are precisely the text that must not reach the brain.
- **The child environment is allowlisted** (`HOME`, `PATH`, locale, `TMPDIR`,
  plus `DISPLAY`/`WAYLAND_DISPLAY`/`XAUTHORITY`). `ANTHROPIC_API_KEY` and
  nested `CLAUDE_CODE_*` variables are dropped.
- **Telemetry is opted out explicitly** (`CUA_TELEMETRY`,
  `CUA_TELEMETRY_ENABLED`) rather than by omission, because an unset variable is
  a default the driver chooses.
- **Driver replies are untrusted input.** Every field is type-checked and handed
  to a bounding value object; a reply that does not fit raises with a constant
  message. Observed `window_id` values are bounded like any other observed
  identifier.

## Composition and the architecture boundary

`apps.worker.app` adds *another `ToolGateway`* and learns nothing about
desktops. Exactly two files may import `friday.infrastructure.computer`:

- `infrastructure/tools/computer_gateway.py` — policy and execution
- `infrastructure/tools/computer_composition.py` — production construction

```python
workspace_gateway = WorkspaceToolGateway(...)
gateways = [workspace_gateway]
computer_gateway = _computer_gateway(runtime)   # build_computer_gateway(...)
if computer_gateway is not None:
    gateways.append(computer_gateway)
gateway = CompositeToolGateway(*gateways)
```

`AgentRunProcessor` and `ExecuteToolAction` receive the **same composite
instance** — asserted by object identity in the tests. Two registries could
disagree about which tools exist or what they cost, and which one won would
depend on where in the call path you looked.

`CompositeToolGateway` stays a policy-free router: it collects descriptors,
detects duplicate names at construction, exposes a deterministic manifest, and
routes `assess`/`execute`. It never reassesses risk or performs fallback
routing.

The brain runtime, application layer, domain layer, and worker loop must never
import computer infrastructure, and native input libraries (pyautogui, pynput,
Quartz/AppKit, UIAutomation, pywinauto, AT-SPI, Xlib, mss, keyboard, mouse) are
forbidden repository-wide. All enforced by `tests/architecture/test_phase13_boundaries.py`.

## Configuration

Computer use defaults **OFF**. When disabled: no driver is constructed, no
cua-driver process is spawned, and zero `computer.*` tools are registered.

When **enabled but unavailable**, worker startup raises
`ComputerUseUnavailable` — before any Run is claimed. An explicitly enabled
capability is never silently downgraded, and there is no fallback to another
input mechanism. (Memory degrades to "no relevant memory found", which is a
truthful answer; a computer gateway that cannot reach a desktop has no truthful
degraded mode.)

| Variable | Default | Meaning |
| --- | --- | --- |
| `FRIDAY_COMPUTER_USE_ENABLED` | `false` | Master switch. |
| `FRIDAY_CUA_DRIVER_CMD` | `cua-driver` | Driver argv, `shlex`-parsed, never run through a shell. |
| `FRIDAY_COMPUTER_TIMEOUT_SECONDS` | `15` | Per-request driver budget. |
| `FRIDAY_COMPUTER_MAX_CAPTURE_BYTES` | `8000000` | Screenshot ceiling, enforced pre-decode. |
| `FRIDAY_COMPUTER_MAX_TYPE_CHARS` | `4096` | `computer.type_text` bound. |
| `FRIDAY_COMPUTER_MAX_SCROLL_DELTA` | `5000` | Operational scroll ceiling (not the ±100000 representable range). |
| `FRIDAY_COMPUTER_CAPTURE_TTL_SECONDS` | `10` | Snapshot fence lifetime; capped at 300. |
| `FRIDAY_COMPUTER_MAX_SNAPSHOTS` | `32` | Total live snapshots. |
| `FRIDAY_COMPUTER_MAX_ELEMENTS` | `500` | Elements per capture. |
| `FRIDAY_CUA_TELEMETRY_ENABLED` | `false` | Passed to the driver explicitly. |

The workspace root comes from `FRIDAY_WORKER_WORKSPACE_ROOT` (RuntimeSettings)
and is deliberately not re-read here — screenshots must land in the same
workspace the file tools are confined to.

## Failure codes

All non-retryable.

| Code | Cause |
| --- | --- |
| `computer_snapshot_not_found` | Unknown or malformed `snapshot_id`. |
| `computer_snapshot_expired` | Past TTL, or dated in the future. |
| `computer_snapshot_mismatch` | Wrong window, wrong run, or an element not in that snapshot. |
| `computer_target_invalid` | Both or neither of `element` / `x`+`y`. |
| `computer_target_out_of_bounds` | Coordinate outside the captured window. |
| `computer_text_rejected` | Oversized, control-bearing, or secret-shaped text. |
| `computer_hotkey_rejected` | Deny-listed combination. |
| `computer_driver_unavailable` | Driver not running or unhealthy. |
| `computer_driver_timeout` | No response within budget. |
| `computer_use_failed` | Driver failure or malformed driver output (sanitized). |
| `tool_invalid_input` | Contract violation (unknown field, wrong type, bad enum). |
| `claim_lost` | Cancellation observed before the driver call. |

Friday's own refusal messages describe its policy and the request, and are
forwarded. Driver and OS messages are **never** forwarded — they can embed
absolute paths, usernames, window contents, transport internals, MCP payloads,
and stack traces.

## Platform support

**Unverified.** The adapter is written against a documented MCP contract and is
tested against a fake transport and real scripted subprocesses; it has not been
run against a real `cua-driver` on any platform as part of this phase. Startup
fails closed if the installed build's tool surface differs, so a mismatch
surfaces as a preflight error rather than misbehaviour.

Before enabling on a real machine, verify: `cua-driver` is installed and on
`PATH`; its MCP `tools/list` advertises the ten names in `CuaToolNames`; and the
OS has granted it the accessibility/screen-recording permissions it needs.
Friday deliberately does **not** automate OS permission dialogs.

`DISPLAY`, `WAYLAND_DISPLAY`, and `XAUTHORITY` are forwarded when set, which is
what a Linux driver needs to reach a session.

## Not in Phase 13

Deliberately absent, not merely unimplemented — each would collapse the fenced
primitive set into a general-purpose escape hatch:

shell / AppleScript execution · clipboard access · credential or password entry
· OS permission-dialog automation · raw keycodes · arbitrary accessibility
commands · arbitrary browser automation · autonomous desktop planning · a
continuous screenshot/action vision loop · vector or vision memory · drag.

There is also no fallback driver. If the configured driver is unavailable,
Friday fails closed.

## Known limitations

1. **Long paths and URLs cannot be typed.** The shared secret detector's token
   class spans `/` and `-`, so a long filesystem path or URL reads as one
   high-entropy token and is refused. Phase 12's curated memory writes depend on
   that same detector, so it is pinned as known behaviour rather than loosened
   here. The failure direction is refusal, which is recoverable.
2. **`meta+l` is a deliberate over-block.** It locks the session on Windows and
   most Linux desktops but is "focus the address bar" on macOS. One normalized
   keystroke cannot mean both, and refusing a useful shortcut is recoverable in
   a way that locking the user out is not.
3. **Snapshots do not survive a worker restart.** By design; see above.
4. **Element truncation is undetectable at the ceiling.** `elements_truncated`
   is derived by requesting one more element than reported, so a `max_elements`
   set exactly to `MAX_ELEMENTS_CEILING` (5000) cannot report truncation. Far
   above any configured limit.
5. **TOCTOU remains.** A snapshot proves what Friday saw within the TTL, not
   that the desktop is unchanged at the instant of the click. Phase 13 provides
   fencing and bounded authority, not a hardened OS sandbox.
6. **Platform support is unproven** (see above).
