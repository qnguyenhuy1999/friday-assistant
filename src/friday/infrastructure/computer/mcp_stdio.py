"""A minimal MCP stdio client: JSON-RPC 2.0 over a child process's stdio.

Written rather than imported. The adapter needs `initialize`, `tools/list`, and
`tools/call` against one locally spawned process — roughly the code below — and
an agent framework would bring a tool-dispatch loop, a prompt abstraction, and
a plugin registry that Friday must not have. §40's "keep the dependency surface
minimal" is the reason; this is also the entire reason there is no generic
`call(action, payload)` reachable from above.

Framing is newline-delimited JSON, per the MCP stdio transport. Every read is
bounded with `readline(limit)`: a line that does not terminate within the limit
is a protocol violation, not something to keep buffering, because the peer is a
subprocess that could otherwise exhaust this process's memory.

Concurrency is one reader thread plus one stderr drain, both daemon. The drain
is not optional — a child that logs verbosely to stderr would otherwise block
forever on a full pipe with nobody reading it, and the symptom would look like
a driver timeout. Requests are strictly serialized: the driver port is
synchronous and one worker executes one tool action at a time, so an id-keyed
response map would model concurrency that does not exist.

Nothing here leaks upward. Every failure becomes a typed ComputerUseError with
a constant message, because a JSON-RPC error body or a stderr line can quote
absolute paths, usernames, and window contents.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import subprocess
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import IO, Protocol

from friday.domain.json_value import JsonValue
from friday.infrastructure.computer.errors import (
    ComputerDriverFailed,
    ComputerDriverTimeout,
    ComputerDriverUnavailable,
)

PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "friday-computer-use"
CLIENT_VERSION = "1"

DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_RESPONSE_BYTES = 16_000_000
_MAX_STDERR_BYTES = 8_000
_SHUTDOWN_GRACE_SECONDS = 2.0

ENVIRONMENT_ALLOWLIST = ("HOME", "PATH", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR")
"""Mirrors the brain runtime's allowlist: the driver needs a home directory and
a PATH, and must not inherit API keys or nested session variables. `DISPLAY`
and `WAYLAND_DISPLAY` are added explicitly when configured, because a Linux
driver cannot reach a session without one."""

_DISPLAY_VARIABLES = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY")


class McpTransport(Protocol):
    """The seam the driver adapter is tested against, so no test needs a real
    subprocess to prove translation, timeout, or malformed-response handling."""

    def start(self) -> None: ...

    def list_tool_names(self) -> tuple[str, ...]: ...

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, JsonValue],
        *,
        require_payload: bool = True,
    ) -> McpToolResult: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class McpStdioSettings:
    argv: tuple[str, ...]
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    extra_env: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.argv or not all(part.strip() for part in self.argv):
            raise ValueError("McpStdioSettings.argv must be a non-empty argument list")
        if self.timeout_seconds <= 0:
            raise ValueError("McpStdioSettings.timeout_seconds must be positive")
        if self.max_response_bytes < 1:
            raise ValueError("McpStdioSettings.max_response_bytes must be positive")


@dataclass(frozen=True, slots=True)
class McpImageContent:
    """One MCP image block, kept separate from a tool's JSON payload."""

    data: str
    media_type: str


@dataclass(frozen=True, slots=True)
class McpToolResult:
    """Lossless subset of an MCP ``CallTool.Result`` envelope.

    A tool may return structured data and an image in the same response.  They
    are complementary, so selecting one at the transport boundary loses
    information the driver adapter needs to build a capture.
    """

    structured_content: JsonValue | None = None
    text_content: JsonValue | None = None
    image_content: tuple[McpImageContent, ...] = ()
    is_error: bool = False


class McpStdioTransport:
    def __init__(self, settings: McpStdioSettings) -> None:
        self._settings = settings
        self._process: subprocess.Popen[bytes] | None = None
        self._lines: queue.Queue[bytes | None] = queue.Queue()
        self._next_id = 0
        self._started = False

    # --- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        try:
            process = subprocess.Popen(  # noqa: S603 - argv list, never a shell
                list(self._settings.argv),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._environment(),
                bufsize=0,
            )
        except (OSError, ValueError) as exc:
            raise ComputerDriverUnavailable("the computer-use driver could not be started") from exc
        self._process = process
        _spawn_daemon(self._pump_stdout, name="friday-mcp-stdout")
        _spawn_daemon(self._drain_stderr, name="friday-mcp-stderr")
        self._started = True
        self._handshake()

    def close(self) -> None:
        process = self._process
        self._process = None
        self._started = False
        if process is None:
            return
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                with contextlib.suppress(OSError):
                    stream.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=_SHUTDOWN_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()

    def _environment(self) -> dict[str, str]:
        allowed = (*ENVIRONMENT_ALLOWLIST, *_DISPLAY_VARIABLES)
        environment = {
            name: os.environ[name] for name in allowed if os.environ.get(name) is not None
        }
        environment.update(self._settings.extra_env)
        return environment

    def _handshake(self) -> None:
        self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        )
        self._notify("notifications/initialized", {})

    # --- MCP surface ------------------------------------------------------

    def list_tool_names(self) -> tuple[str, ...]:
        """Read the advertised tool names, strictly.

        A malformed entry is refused rather than skipped. This list is what the
        driver adapter's startup health check verifies its required tools
        against, so quietly dropping an entry Friday could not parse would mean
        preflighting against an incomplete picture of what the driver exposes —
        and concluding "tool missing" or "tool present" on incomplete evidence.
        """
        result = self._request("tools/list", {})
        if not isinstance(result, dict):
            raise ComputerDriverFailed("the computer-use driver returned a malformed tool list")
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise ComputerDriverFailed("the computer-use driver returned a malformed tool list")
        names: list[str] = []
        for entry in tools:
            if not isinstance(entry, dict):
                raise ComputerDriverFailed("the computer-use driver returned a malformed tool list")
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                raise ComputerDriverFailed("the computer-use driver returned a malformed tool list")
            names.append(name)
        return tuple(names)

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, JsonValue],
        *,
        require_payload: bool = True,
    ) -> McpToolResult:
        """Invoke one MCP tool and preserve its complete useful envelope.

        Private by convention and by boundary: the driver adapter is the only
        caller, and it only ever passes names from its own fixed mapping. No
        layer above can reach a tool Friday did not declare.

        Structured JSON, text JSON, and image blocks are intentionally retained
        together.  In particular, `get_window_state` supplies its elements in
        `structuredContent` and its screenshot in an image block.

        `require_payload=False` says exactly that, and is used for mutating
        calls: `isError` still raises, but a reply carrying only a summary line
        returns None rather than raising. A click that already moved the desktop
        must not be reported as a driver failure because its receipt was prose.
        """
        result = self._request("tools/call", {"name": name, "arguments": dict(arguments)})
        if not isinstance(result, dict):
            raise ComputerDriverFailed("the computer-use driver returned a malformed result")
        if result.get("isError") is True:
            # the body may quote OS paths or window text; report nothing from it
            raise ComputerDriverFailed("the computer-use driver reported a tool error")
        structured = result.get("structuredContent")
        text = _decode_text_content_or_none(result.get("content"))
        images = _image_content(result.get("content"))
        if require_payload and structured is None and text is None:
            raise ComputerDriverFailed("the computer-use driver returned no usable content")
        return McpToolResult(
            structured_content=structured,
            text_content=text,
            image_content=images,
            is_error=False,
        )

    # --- JSON-RPC ---------------------------------------------------------

    def _request(self, method: str, params: Mapping[str, JsonValue]) -> JsonValue:
        self._next_id += 1
        request_id = self._next_id
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params)})
        return self._await_response(request_id)

    def _notify(self, method: str, params: Mapping[str, JsonValue]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": dict(params)})

    def _write(self, message: Mapping[str, JsonValue]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise ComputerDriverUnavailable("the computer-use driver is not running")
        payload = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
        try:
            process.stdin.write(payload)
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ComputerDriverUnavailable(
                "the computer-use driver stopped accepting input"
            ) from exc

    def _await_response(self, request_id: int) -> JsonValue:
        """Read until the matching response arrives, the budget expires, or the
        process dies. Notifications and server-initiated requests are skipped
        rather than treated as answers."""
        while True:
            line = self._next_line()
            try:
                message = json.loads(line)
            except (UnicodeDecodeError, ValueError) as exc:
                raise ComputerDriverFailed(
                    "the computer-use driver sent a malformed message"
                ) from exc
            if not isinstance(message, dict):
                raise ComputerDriverFailed("the computer-use driver sent a malformed message")
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise ComputerDriverFailed("the computer-use driver returned an error response")
            if "result" not in message:
                raise ComputerDriverFailed("the computer-use driver returned no result")
            result: JsonValue = message["result"]
            return result

    def _next_line(self) -> bytes:
        try:
            line = self._lines.get(timeout=self._settings.timeout_seconds)
        except queue.Empty:
            raise ComputerDriverTimeout("the computer-use driver did not respond in time") from None
        if line is None:
            raise ComputerDriverUnavailable("the computer-use driver exited unexpectedly")
        return line

    # --- background readers ----------------------------------------------

    def _pump_stdout(self) -> None:
        process = self._process
        stream = process.stdout if process is not None else None
        if stream is None:
            self._lines.put(None)
            return
        limit = self._settings.max_response_bytes
        try:
            while True:
                line = stream.readline(limit)
                if not line:
                    break
                if not line.endswith(b"\n"):
                    # either the stream ended mid-line or the line exceeded the
                    # response ceiling; both mean this connection is unusable
                    break
                stripped = line.strip()
                if stripped:
                    self._lines.put(stripped)
        except (OSError, ValueError):
            pass
        self._lines.put(None)

    def _drain_stderr(self) -> None:
        process = self._process
        stream = process.stderr if process is not None else None
        if stream is None:
            return
        _drain(stream)


def _drain(stream: IO[bytes]) -> None:
    """Consume and discard stderr so the child never blocks on a full pipe.

    Discarded rather than captured: driver diagnostics are exactly the text
    that must not reach the brain, and Friday has no use for them that would
    justify holding them in memory.
    """
    try:
        while stream.read(_MAX_STDERR_BYTES):
            pass
    except (OSError, ValueError):
        pass


def _spawn_daemon(target: object, *, name: str) -> None:
    assert callable(target)
    thread = threading.Thread(target=target, name=name, daemon=True)
    thread.start()


def _decode_text_content_or_none(content: JsonValue) -> JsonValue | None:
    """Decode the first JSON text block, ignoring prose summaries.

    MCP text blocks are normally human-readable summaries. They are only a
    payload fallback when one happens to contain JSON; an image block must not
    make the result malformed.
    """
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if not isinstance(text, str):
            continue
        try:
            decoded: JsonValue = json.loads(text)
        except ValueError:
            continue
        return decoded
    return None


def _image_content(content: JsonValue) -> tuple[McpImageContent, ...]:
    """Read valid image blocks without flattening them into JSON text."""
    if not isinstance(content, list):
        return ()
    images: list[McpImageContent] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "image":
            continue
        data = block.get("data")
        media_type = block.get("mimeType")
        if not isinstance(data, str) or not isinstance(media_type, str):
            raise ComputerDriverFailed("the computer-use driver returned a malformed image block")
        images.append(McpImageContent(data=data, media_type=media_type))
    return tuple(images)
