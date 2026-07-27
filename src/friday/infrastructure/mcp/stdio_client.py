"""Bounded JSON-RPC 2.0 stdio transport for a single allow-listed argv child."""

from __future__ import annotations

import contextlib
import json
import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from typing import IO, cast

from friday.domain.json_value import JsonValue
from friday.infrastructure.mcp.client import (
    CLIENT_NAME,
    CLIENT_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    McpCallResult,
    McpRemoteTool,
)
from friday.infrastructure.mcp.config import McpServerConfig
from friday.infrastructure.mcp.errors import (
    McpCallTimeout,
    McpConnectTimeout,
    McpError,
    McpProtocolError,
    McpRemoteError,
    McpUnavailable,
)

BASE_ENVIRONMENT_ALLOWLIST = (
    "HOME",
    "PATH",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
)
_MAX_LINE_BYTES = 8_000_000
_SHUTDOWN_GRACE_SECONDS = 2.0


class McpStdioClient:
    def __init__(
        self, server: McpServerConfig, *, base_environment: Mapping[str, str] | None = None
    ) -> None:
        self._server = server
        self._base_environment = base_environment
        self._process: subprocess.Popen[bytes] | None = None
        self._lines: queue.Queue[bytes | None] = queue.Queue()
        self._next_id = 0
        self._protocol_version: str | None = None
        self._closed = False

    def connect(self) -> str:
        if self._protocol_version is not None:
            return self._protocol_version
        if self._closed:
            raise McpUnavailable("the MCP server connection is closed")
        try:
            self._process = subprocess.Popen(
                list(self._server.command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._environment(),
                bufsize=0,
            )  # noqa: S603
        except (OSError, ValueError) as exc:
            raise McpUnavailable("the MCP server could not be started") from exc
        _spawn_daemon(self._pump_stdout)
        _spawn_daemon(self._drain_stderr)
        result = self._request(
            "initialize",
            {
                "protocolVersion": SUPPORTED_PROTOCOL_VERSIONS[0],
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
            timeout_seconds=self._server.connect_timeout_seconds,
            timeout_error=McpConnectTimeout,
        )
        if (
            not isinstance(result, dict)
            or not isinstance((version := result.get("protocolVersion")), str)
            or version not in SUPPORTED_PROTOCOL_VERSIONS
        ):
            raise McpProtocolError("the MCP server returned a malformed handshake")
        self._protocol_version = version
        self._notify("notifications/initialized", {})
        return version

    def close(self) -> None:
        self._closed = True
        process, self._process = self._process, None
        self._protocol_version = None
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
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=_SHUTDOWN_GRACE_SECONDS)

    def child_pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    def list_tools(self) -> tuple[McpRemoteTool, ...]:
        self._require_connected()
        result = self._request(
            "tools/list",
            {},
            timeout_seconds=self._server.connect_timeout_seconds,
            timeout_error=McpConnectTimeout,
        )
        if (
            not isinstance(result, dict)
            or not isinstance((entries := result.get("tools")), list)
            or len(entries) > self._server.max_discovered_tools
        ):
            raise McpProtocolError("the MCP server returned a malformed tool list")
        if (
            len(json.dumps(entries, separators=(",", ":")).encode())
            > self._server.discovery_byte_budget
        ):
            raise McpProtocolError("the MCP server tool list exceeded its byte budget")
        tools: list[McpRemoteTool] = []
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or not isinstance((name := entry.get("name")), str)
                or not name.strip()
            ):
                raise McpProtocolError("the MCP server returned a malformed tool list")
            tools.append(McpRemoteTool(name=name, input_schema=entry.get("inputSchema")))
        return tuple(tools)

    def call_tool(
        self,
        name: str,
        arguments: dict[str, JsonValue],
        *,
        timeout_seconds: float,
        cancelled: Callable[[], bool] | None = None,
    ) -> McpCallResult:
        self._require_connected()
        result = self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout_seconds=timeout_seconds,
            timeout_error=McpCallTimeout,
            cancelled=cancelled,
        )
        if not isinstance(result, dict):
            raise McpProtocolError("the MCP server returned a malformed result")
        if result.get("isError") is True:
            raise McpRemoteError("the MCP server reported a tool error")
        content = result.get("content")
        return McpCallResult(
            result.get("structuredContent"), _text_blocks(content), _omitted_kinds(content)
        )

    def _environment(self) -> dict[str, str]:
        source = os.environ if self._base_environment is None else self._base_environment
        return {
            name: source[name]
            for name in (*BASE_ENVIRONMENT_ALLOWLIST, *self._server.env_from)
            if source.get(name) is not None
        }

    def _require_connected(self) -> None:
        if self._process is None or self._protocol_version is None:
            raise McpUnavailable("the MCP server is not connected")

    def _request(
        self,
        method: str,
        params: Mapping[str, JsonValue],
        *,
        timeout_seconds: float,
        timeout_error: type[McpError],
        cancelled: Callable[[], bool] | None = None,
    ) -> JsonValue:
        self._next_id += 1
        request_id = self._next_id
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params)})
        deadline = time.monotonic() + timeout_seconds
        while True:
            if cancelled is not None and cancelled():
                raise timeout_error("the MCP call was cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise timeout_error("the MCP server did not respond in time")
            try:
                line = self._lines.get(timeout=min(remaining, 0.05))
            except queue.Empty:
                continue
            if line is None:
                raise McpUnavailable("the MCP server exited unexpectedly")
            try:
                message = json.loads(line)
            except (UnicodeDecodeError, ValueError) as exc:
                raise McpProtocolError("the MCP server sent a malformed message") from exc
            if not isinstance(message, dict):
                raise McpProtocolError("the MCP server sent a malformed message")
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise McpRemoteError("the MCP server returned an error response")
            if "result" not in message:
                raise McpProtocolError("the MCP server returned no result")
            return cast(JsonValue, message["result"])

    def _notify(self, method: str, params: Mapping[str, JsonValue]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": dict(params)})

    def _write(self, message: Mapping[str, JsonValue]) -> None:
        if self._process is None or self._process.stdin is None:
            raise McpUnavailable("the MCP server is not running")
        try:
            self._process.stdin.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise McpUnavailable("the MCP server stopped accepting input") from exc

    def _pump_stdout(self) -> None:
        stream = self._process.stdout if self._process is not None else None
        if stream is not None:
            try:
                while line := stream.readline(_MAX_LINE_BYTES):
                    if not line.endswith(b"\n"):
                        break
                    if line.strip():
                        self._lines.put(line.strip())
            except (OSError, ValueError):
                pass
        self._lines.put(None)

    def _drain_stderr(self) -> None:
        stream = self._process.stderr if self._process is not None else None
        if stream is not None:
            _drain(stream)


def _spawn_daemon(target: Callable[[], None]) -> None:
    threading.Thread(target=target, daemon=True).start()


def _drain(stream: IO[bytes]) -> None:
    try:
        while stream.read(8_000):
            pass
    except (OSError, ValueError):
        pass


def _text_blocks(content: JsonValue) -> tuple[str, ...]:
    if not isinstance(content, list):
        return ()
    texts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                texts.append(text)
    return tuple(texts)


def _omitted_kinds(content: JsonValue) -> tuple[str, ...]:
    if not isinstance(content, list):
        return ()
    kinds: set[str] = set()
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            continue
        kind = block.get("type") if isinstance(block, dict) else None
        kinds.add(kind if isinstance(kind, str) else "unknown")
    return tuple(sorted(kinds))
