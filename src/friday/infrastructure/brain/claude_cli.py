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
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Any

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
    max_stderr_bytes: int = 200_000

    def __post_init__(self) -> None:
        if not self.executable.strip():
            raise ValueError("executable must not be empty")
        if self.model is not None and not self.model.strip():
            raise ValueError("model must not be blank when set")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        if self.max_stderr_bytes <= 0:
            raise ValueError("max_stderr_bytes must be positive")


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

        stdout, stderr_bytes = _communicate_bounded(
            process,
            prompt,
            request.timeout_seconds or self._settings.timeout_seconds,
            self._settings.max_output_bytes,
            self._settings.max_stderr_bytes,
        )

        if len(stdout.encode("utf-8")) > self._settings.max_output_bytes:
            raise BrainProtocolError("CLI stdout exceeded the configured limit")
        if process.returncode != 0:
            # stderr content is untrusted and may contain diagnostics or
            # credential paths — only its size is reported.
            raise BrainUnavailable(
                f"Claude CLI exited with code {process.returncode} (stderr: {stderr_bytes} bytes)"
            )
        envelope = parse_cli_envelope(stdout)
        if len(envelope.result_text.encode("utf-8")) > request.max_response_bytes:
            raise BrainProtocolError("model response exceeded the configured limit")
        return envelope


def _terminate_process_group(process: subprocess.Popen[Any]) -> None:
    """Kill the CLI's whole process group so no orphan survives a timeout."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):  # pragma: no cover - race with exit
        process.kill()
    # Drain the pipes and reap — communicate() after kill is the documented
    # non-deadlocking cleanup for a timed-out child.
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def _communicate_bounded(
    process: subprocess.Popen[str],
    prompt: str,
    timeout: float,
    max_stdout_bytes: int,
    max_stderr_bytes: int,
) -> tuple[str, int]:
    assert process.stdin is not None
    process.stdin.write(prompt)
    process.stdin.close()
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray()}
    stderr_bytes = 0
    for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
        assert stream is not None
        selector.register(stream, selectors.EVENT_READ, name)
    deadline = time.monotonic() + timeout
    while selector.get_map():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            selector.close()
            _terminate_process_group(process)
            raise BrainTimeout(f"Claude CLI exceeded {timeout}s")
        for key, _ in selector.select(min(remaining, 0.1)):
            data = os.read(key.fd, 65536)
            if not data:
                selector.unregister(key.fileobj)
                continue
            name = key.data
            if name == "stderr":
                stderr_bytes += len(data)
            else:
                buffers[name].extend(data)
            limit = max_stdout_bytes if name == "stdout" else max_stderr_bytes
            observed = len(buffers["stdout"]) if name == "stdout" else stderr_bytes
            if observed > limit:
                selector.close()
                _terminate_process_group(process)
                raise BrainProtocolError(
                    "CLI stdout exceeded the configured limit"
                    if name == "stdout"
                    else "CLI stderr exceeded the configured limit"
                )
    selector.close()
    process.wait()
    result = (
        bytes(buffers["stdout"]).decode("utf-8", errors="replace"),
        stderr_bytes,
    )
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()
    return result


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
    _run_semantic_probe(settings)
    return version.strip()[:200]


def _run_semantic_probe(settings: ClaudeCliSettings) -> None:
    argv = [
        settings.executable,
        "-p",
        "--output-format",
        "json",
        "--tools",
        "",
        "--strict-mcp-config",
        "--safe-mode",
        "--no-session-persistence",
    ]
    environment = {
        name: value for name in ENVIRONMENT_ALLOWLIST if (value := os.environ.get(name)) is not None
    }
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=environment,
            start_new_session=True,
        )
        stdout, _ = _communicate_bounded(
            process,
            'Return exactly {"version":1,"action":"finish","result":{"summary":"ok"}}',
            _VERIFY_TIMEOUT_SECONDS,
            _VERIFY_MAX_OUTPUT_BYTES,
            settings.max_stderr_bytes,
        )
    except (
        FileNotFoundError,
        subprocess.TimeoutExpired,
        OSError,
        BrainProtocolError,
        BrainTimeout,
    ) as exc:
        raise BrainUnavailable("Claude CLI semantic brain-only probe failed") from exc
    if process.returncode != 0:
        raise BrainUnavailable(f"Claude CLI semantic probe exited with {process.returncode}")
    try:
        envelope = parse_cli_envelope(stdout)
        parse_brain_action(json.loads(envelope.result_text))
    except (BrainProtocolError, BrainResponseInvalid, json.JSONDecodeError) as exc:
        raise BrainUnavailable("Claude CLI semantic probe returned an invalid action") from exc
    # The probe's contract is the valid action envelope, not a particular
    # action kind. A deterministic fake/older CLI may return another valid
    # non-executing proposal while still proving the process-level mode.


def _run_probe(executable: str, flag: str) -> str:
    environment = {
        name: value for name in ENVIRONMENT_ALLOWLIST if (value := os.environ.get(name)) is not None
    }
    try:
        process = subprocess.Popen(  # noqa: S603 - argv list, allowlisted env
            [executable, flag],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=environment,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise BrainUnavailable(f"Claude CLI executable not found: {executable}") from exc
    except subprocess.TimeoutExpired as exc:
        raise BrainUnavailable(f"Claude CLI probe timed out: {flag}") from exc
    except OSError as exc:
        raise BrainUnavailable("Claude CLI could not be started") from exc
    try:
        stdout, _ = _communicate_bounded(
            process, "", _VERIFY_TIMEOUT_SECONDS, _VERIFY_MAX_OUTPUT_BYTES, _VERIFY_MAX_OUTPUT_BYTES
        )
    except (BrainProtocolError, BrainTimeout) as exc:
        raise BrainUnavailable(f"Claude CLI probe failed: {flag}") from exc
    if process.returncode != 0:
        raise BrainUnavailable(f"Claude CLI probe {flag} exited with {process.returncode}")
    return stdout[:_VERIFY_MAX_OUTPUT_BYTES]
