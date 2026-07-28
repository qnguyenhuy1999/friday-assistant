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

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from friday.domain.json_value import JsonValue
from friday.infrastructure.computer.errors import (
    ComputerDriverFailed,
    ComputerDriverTimeout,
    ComputerDriverUnavailable,
)
from friday.infrastructure.process.stdio_jsonrpc import (
    StdioJsonRpcSession,
    StdioSessionProtocolError,
    StdioSessionRemoteError,
    StdioSessionSettings,
    StdioSessionTimeout,
    StdioSessionUnavailable,
)

PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "friday-computer-use"
CLIENT_VERSION = "1"

DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_RESPONSE_BYTES = 16_000_000

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
        self._started = False
        self._session: StdioJsonRpcSession | None = None

    # --- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        environment = self._environment()
        session = StdioJsonRpcSession(
            StdioSessionSettings(
                argv=self._settings.argv,
                environment=environment,
                max_line_bytes=self._settings.max_response_bytes,
            )
        )
        try:
            self._session = session
            session.start(handshake=self._handshake)
        except (StdioSessionUnavailable, StdioSessionProtocolError, StdioSessionRemoteError) as exc:
            self._session = None
            raise ComputerDriverUnavailable("the computer-use driver could not be started") from exc
        except StdioSessionTimeout as exc:
            self._session = None
            raise ComputerDriverTimeout("the computer-use driver did not connect in time") from exc
        self._started = True

    def close(self) -> None:
        if self._session is not None:
            session, self._session = self._session, None
            self._started = False
            session.close()
            return
        self._started = False

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
        if self._session is not None:
            try:
                return self._session.request(
                    method, params, timeout_seconds=self._settings.timeout_seconds
                )
            except StdioSessionTimeout as exc:
                raise ComputerDriverTimeout(
                    "the computer-use driver did not respond in time"
                ) from exc
            except StdioSessionUnavailable as exc:
                raise ComputerDriverUnavailable(
                    "the computer-use driver exited unexpectedly"
                ) from exc
            except (StdioSessionProtocolError, StdioSessionRemoteError) as exc:
                raise ComputerDriverFailed(
                    "the computer-use driver returned an unusable response"
                ) from exc
        raise ComputerDriverUnavailable("the computer-use driver is not running")

    def _notify(self, method: str, params: Mapping[str, JsonValue]) -> None:
        if self._session is not None:
            try:
                self._session.notify(method, params, timeout_seconds=self._settings.timeout_seconds)
                return
            except StdioSessionTimeout as exc:
                raise ComputerDriverTimeout(
                    "the computer-use driver did not accept input in time"
                ) from exc
            except StdioSessionUnavailable as exc:
                raise ComputerDriverUnavailable(
                    "the computer-use driver stopped accepting input"
                ) from exc
            except (StdioSessionProtocolError, StdioSessionRemoteError) as exc:
                raise ComputerDriverFailed(
                    "the computer-use driver returned an unusable response"
                ) from exc


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
