"""Claude CLI brain adapter: the concrete BrainRuntime backend.

Brain-only enforcement is process-level, not prompt-level: every invocation
passes ``--tools ""`` (disables ALL built-in tools), ``--strict-mcp-config``
with no MCP config (no MCP servers), ``--safe-mode`` (no hooks, plugins,
CLAUDE.md auto-discovery, or custom commands), and ``--no-session-persistence``.
The CLI can therefore only return text — it cannot edit files, run shell
commands, or call tools, regardless of what the model asks for.

Authentication uses the locally authenticated claude.ai subscription (OAuth
credentials on disk). The subprocess environment is built from an explicit
allowlist; ``ANTHROPIC_API_KEY`` and nested-session ``CLAUDE_CODE_*``
variables are never inherited, so the adapter cannot silently fall back to
API-key billing.

Prompts and responses are never logged. Errors carry bounded, content-free
messages only."""

from __future__ import annotations

import json
import os
import signal
import subprocess
from dataclasses import dataclass

from friday.application.brain_runtime import BrainRequest, BrainResponse
from friday.application.errors import (
    BrainProtocolError,
    BrainResponseInvalid,
    BrainTimeout,
    BrainUnavailable,
)
from friday.application.runtime_actions import parse_brain_action
from friday.infrastructure.brain.claude_cli_protocol import CliEnvelope, parse_cli_envelope

ENVIRONMENT_ALLOWLIST = (
    "HOME",
    "PATH",
    "USER",
    "LOGNAME",
    "SHELL",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
)
"""Environment variables the CLI subprocess may inherit. HOME locates the
on-disk claude.ai subscription credentials; PATH locates the executable's
runtime. Everything else — including ANTHROPIC_API_KEY and any nested
CLAUDE_CODE_* session variables — is deliberately dropped."""

_SYSTEM_PROMPT = (
    "You are the reasoning engine of Friday Agent OS. You never execute "
    "anything yourself; Friday validates, authorizes, and executes every "
    "action. Each turn, read the context document and respond with EXACTLY "
    "one JSON object — no prose, no markdown, no code fences. The object "
    'must match one of: {"version":1,"action":"finish","result":{"summary":'
    '"<what was accomplished>"}} | {"version":1,"action":"fail","reason":'
    '"<why the task cannot proceed>"} | {"version":1,"action":"yield",'
    '"delay_seconds":<0-86400>,"reason":"<optional>"} | {"version":1,'
    '"action":"invoke_tool","tool":"<name from the # TOOLS section>",'
    '"input":{<tool input object>},"reason":"<optional>"}. '
    "Use only tools listed in the # TOOLS section. Tool outputs appear in "
    "the # TOOL INVOCATIONS section of the next turn's context."
)

_REPAIR_PREAMBLE = (
    "Your previous response was not a valid action envelope. "
    "Reply with EXACTLY one corrected JSON object and nothing else.\n"
    "Validation error: {error}\n"
    "Previous response (may be truncated):\n{previous}"
)
_MAX_REPAIR_ECHO_CHARS = 2000


@dataclass(frozen=True, slots=True)
class ClaudeCliSettings:
    """Adapter configuration. No secret-bearing field exists by design."""

    executable: str
    model: str | None
    timeout_seconds: float
    max_output_bytes: int

    def __post_init__(self) -> None:
        if not self.executable.strip():
            raise ValueError("executable must not be empty")
        if self.model is not None and not self.model.strip():
            raise ValueError("model must not be blank when set")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")


class ClaudeCliBrainRuntime:
    """BrainRuntime backed by the locally authenticated Claude CLI."""

    def __init__(self, settings: ClaudeCliSettings) -> None:
        self._settings = settings

    def next_action(self, request: BrainRequest) -> BrainResponse:
        envelope = self._invoke(self._render_prompt(request), request)
        try:
            action = parse_brain_action(_decode_action_json(envelope.result_text))
        except BrainResponseInvalid as first_error:
            envelope = self._invoke(_repair_prompt(first_error, envelope.result_text), request)
            action = parse_brain_action(_decode_action_json(envelope.result_text))
            return BrainResponse(
                action=action, model=envelope.model, usage=envelope.usage, repaired=True
            )
        return BrainResponse(action=action, model=envelope.model, usage=envelope.usage)

    def _render_prompt(self, request: BrainRequest) -> str:
        return (
            f"{request.context}\n\n"
            "Respond with exactly one JSON action object now. "
            "No prose, no markdown, no code fences."
        )

    def _argv(self) -> list[str]:
        argv = [
            self._settings.executable,
            "-p",
            "--output-format",
            "json",
            "--tools",
            "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--safe-mode",
            "--system-prompt",
            _SYSTEM_PROMPT,
        ]
        if self._settings.model is not None:
            argv.extend(["--model", self._settings.model])
        return argv

    def _environment(self) -> dict[str, str]:
        return {
            name: value
            for name in ENVIRONMENT_ALLOWLIST
            if (value := os.environ.get(name)) is not None
        }

    def _invoke(self, prompt: str, request: BrainRequest) -> CliEnvelope:
        try:
            process = subprocess.Popen(
                self._argv(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._environment(),
                text=True,
                encoding="utf-8",
                start_new_session=True,
            )
        except OSError as exc:
            raise BrainUnavailable("Claude CLI could not be started") from exc

        try:
            stdout, stderr = process.communicate(
                input=prompt, timeout=self._settings.timeout_seconds
            )
        except subprocess.TimeoutExpired as exc:
            _terminate_process_group(process)
            raise BrainTimeout(f"Claude CLI exceeded {self._settings.timeout_seconds}s") from exc

        if len(stdout.encode("utf-8")) > self._settings.max_output_bytes:
            raise BrainProtocolError("CLI stdout exceeded the configured limit")
        if process.returncode != 0:
            # stderr content is untrusted and may contain diagnostics or
            # credential paths — only its size is reported.
            raise BrainUnavailable(
                f"Claude CLI exited with code {process.returncode}"
                f" (stderr: {len(stderr.encode('utf-8'))} bytes)"
            )
        envelope = parse_cli_envelope(stdout)
        if len(envelope.result_text.encode("utf-8")) > request.max_response_bytes:
            raise BrainProtocolError("model response exceeded the configured limit")
        return envelope


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    """Kill the CLI's whole process group so no orphan survives a timeout."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):  # pragma: no cover - race with exit
        process.kill()
    # Drain the pipes and reap — communicate() after kill is the documented
    # non-deadlocking cleanup for a timed-out child.
    process.communicate()


def _decode_action_json(result_text: str) -> object:
    try:
        return json.loads(result_text)
    except json.JSONDecodeError as exc:
        raise BrainResponseInvalid("model response is not valid JSON") from exc


def _repair_prompt(error: BrainResponseInvalid, previous: str) -> str:
    clipped = previous[:_MAX_REPAIR_ECHO_CHARS]
    return _REPAIR_PREAMBLE.format(error=error, previous=clipped)


REQUIRED_CLI_FLAGS = (
    "--print",
    "--output-format",
    "--tools",
    "--safe-mode",
    "--strict-mcp-config",
    "--no-session-persistence",
    "--system-prompt",
)
"""Flags the installed CLI must advertise for brain-only operation to be
guaranteed at the process level. Missing any of them fails startup closed."""

_VERIFY_TIMEOUT_SECONDS = 30.0
_VERIFY_MAX_OUTPUT_BYTES = 1_000_000


def verify_brain_only_support(settings: ClaudeCliSettings) -> str:
    """Fail-closed startup verification: the executable must exist, run, and
    advertise every flag brain-only mode depends on. Returns the CLI version
    string. Raises BrainUnavailable otherwise — a worker must never claim a
    Run with an unverified brain."""
    version = _run_probe(settings.executable, "--version")
    help_text = _run_probe(settings.executable, "--help")
    missing = [flag for flag in REQUIRED_CLI_FLAGS if flag not in help_text]
    if missing:
        raise BrainUnavailable(
            f"Claude CLI does not advertise required brain-only flag(s): {missing}"
        )
    return version.strip()[:200]


def _run_probe(executable: str, flag: str) -> str:
    environment = {
        name: value for name in ENVIRONMENT_ALLOWLIST if (value := os.environ.get(name)) is not None
    }
    try:
        completed = subprocess.run(  # noqa: S603 - argv list, allowlisted env
            [executable, flag],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            timeout=_VERIFY_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BrainUnavailable(f"Claude CLI executable not found: {executable}") from exc
    except subprocess.TimeoutExpired as exc:
        raise BrainUnavailable(f"Claude CLI probe timed out: {flag}") from exc
    except OSError as exc:
        raise BrainUnavailable("Claude CLI could not be started") from exc
    if completed.returncode != 0:
        raise BrainUnavailable(f"Claude CLI probe {flag} exited with {completed.returncode}")
    return completed.stdout[:_VERIFY_MAX_OUTPUT_BYTES]
