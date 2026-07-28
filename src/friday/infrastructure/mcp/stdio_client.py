"""MCP protocol adapter over the one shared stdio JSON-RPC transport."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from typing import cast

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
from friday.infrastructure.mcp.redaction import SensitiveValueRedactor
from friday.infrastructure.process.stdio_jsonrpc import (
    StdioJsonRpcSession,
    StdioSessionError,
    StdioSessionProtocolError,
    StdioSessionRemoteError,
    StdioSessionSettings,
    StdioSessionTimeout,
    allowlisted_environment,
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


class McpStdioClient:
    def __init__(
        self, server: McpServerConfig, *, base_environment: Mapping[str, str] | None = None
    ) -> None:
        self._server = server
        self._base_environment = base_environment
        self._session: StdioJsonRpcSession | None = None
        self._protocol_version: str | None = None
        self._closed = False
        self._redactor = SensitiveValueRedactor.from_values(())
        self._execution_identity: dict[str, JsonValue] = {}
        # Resolve the credential identity at composition time, before any
        # external output can be discovered. connect() resolves it again just
        # before spawn in case an operator intentionally rotates it meanwhile.
        self._environment()

    def connect(self) -> str:
        if self._protocol_version is not None:
            return self._protocol_version
        if self._closed:
            raise McpUnavailable("the MCP server connection is closed")
        environment = self._environment()
        session = StdioJsonRpcSession(
            StdioSessionSettings(
                argv=self._server.command,
                environment=environment,
                max_line_bytes=max(
                    self._server.discovery_byte_budget, self._server.max_output_bytes
                ),
            )
        )
        try:
            self._session = session
            session.start(handshake=self._initialize)
            assert self._protocol_version is not None
            self._notify("notifications/initialized", {})
            return self._protocol_version
        except McpError:
            self.close()
            raise
        except StdioSessionError as exc:
            self.close()
            raise _translate(exc, McpConnectTimeout) from exc

    def close(self) -> None:
        self._closed = True
        session, self._session = self._session, None
        self._protocol_version = None
        if session is not None:
            session.close()

    def child_pid(self) -> int | None:
        return self._session.pid if self._session is not None else None

    def execution_identity(self) -> JsonValue:
        return dict(self._execution_identity)

    def list_tools(self) -> tuple[McpRemoteTool, ...]:
        result = self._request(
            "tools/list", {}, self._server.connect_timeout_seconds, McpConnectTimeout
        )
        if not isinstance(result, dict) or not isinstance((entries := result.get("tools")), list):
            raise McpProtocolError("the MCP server returned a malformed tool list")
        if (
            len(entries) > self._server.max_discovered_tools
            or _json_bytes(entries) > self._server.discovery_byte_budget
        ):
            raise McpProtocolError("the MCP server tool list exceeded its configured budget")
        tools: list[McpRemoteTool] = []
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or not isinstance((name := entry.get("name")), str)
                or not name.strip()
                or "inputSchema" not in entry
            ):
                raise McpProtocolError("the MCP server returned a malformed tool list")
            tools.append(McpRemoteTool(name=name, input_schema=entry["inputSchema"]))
        return tuple(tools)

    def call_tool(
        self,
        name: str,
        arguments: dict[str, JsonValue],
        *,
        timeout_seconds: float,
        cancelled: Callable[[], bool] | None = None,
    ) -> McpCallResult:
        result = self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout_seconds,
            McpCallTimeout,
            cancelled,
        )
        if not isinstance(result, dict):
            raise McpProtocolError("the MCP server returned a malformed result")
        is_error = result.get("isError", False)
        content = result.get("content")
        structured = result.get("structuredContent")
        if (
            not isinstance(is_error, bool)
            or not isinstance(content, list)
            or len(content) > self._server.max_content_items
            or (structured is not None and not isinstance(structured, dict))
            or any(not isinstance(block, dict) for block in content)
        ):
            raise McpProtocolError("the MCP server returned a malformed result")
        blocks: list[dict[str, JsonValue]] = []
        for block in content:
            if not isinstance(block, dict):
                raise McpProtocolError("the MCP server returned a malformed result")
            if block.get("type") == "text" and not isinstance(block.get("text"), str):
                raise McpProtocolError("the MCP server returned a malformed result")
            blocks.append(block)
        if is_error:
            raise McpRemoteError("the MCP server reported a tool error")
        return McpCallResult(
            cast(JsonValue | None, structured),
            _text_blocks(blocks),
            _omitted_kinds(blocks),
            len(content),
        )

    def _initialize(self) -> None:
        result = self._request(
            "initialize",
            {
                "protocolVersion": SUPPORTED_PROTOCOL_VERSIONS[0],
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
            self._server.connect_timeout_seconds,
            McpConnectTimeout,
        )
        if (
            not isinstance(result, dict)
            or not isinstance((version := result.get("protocolVersion")), str)
            or version not in SUPPORTED_PROTOCOL_VERSIONS
        ):
            raise McpProtocolError("the MCP server returned a malformed handshake")
        self._protocol_version = version
        self._execution_identity["protocol_version"] = version

    def _request(
        self,
        method: str,
        params: Mapping[str, JsonValue],
        timeout: float,
        timeout_error: type[McpError],
        cancelled: Callable[[], bool] | None = None,
    ) -> JsonValue:
        if self._session is None:
            raise McpUnavailable("the MCP server is not connected")
        try:
            result = self._session.request(
                method, params, timeout_seconds=timeout, cancelled=cancelled
            )
        except StdioSessionError as exc:
            self.close()
            raise _translate(exc, timeout_error) from exc
        return self._redactor.redact(result)

    def _notify(self, method: str, params: Mapping[str, JsonValue]) -> None:
        if self._session is None:
            raise McpUnavailable("the MCP server is not connected")
        try:
            self._session.notify(
                method,
                params,
                timeout_seconds=self._server.connect_timeout_seconds,
            )
        except StdioSessionError as exc:
            self.close()
            raise _translate(exc, McpConnectTimeout) from exc

    def _environment(self) -> dict[str, str]:
        source = os.environ if self._base_environment is None else self._base_environment
        environment = allowlisted_environment(
            (*BASE_ENVIRONMENT_ALLOWLIST, *self._server.env_from), source=source
        )
        values = tuple(environment[name] for name in self._server.env_from if name in environment)
        self._redactor = SensitiveValueRedactor.from_values(values)
        resolved = shutil.which(self._server.command[0], path=environment.get("PATH"))
        self._execution_identity = {
            "cwd": os.path.realpath(os.getcwd()),
            "executable": os.path.realpath(resolved) if resolved else self._server.command[0],
            "principal": _credential_identity(values, source.get("FRIDAY_MCP_IDENTITY_KEY")),
        }
        return environment


def _translate(error: StdioSessionError, timeout: type[McpError]) -> McpError:
    if isinstance(error, StdioSessionTimeout):
        return timeout("the MCP server did not respond in time")
    if isinstance(error, StdioSessionProtocolError):
        return McpProtocolError("the MCP server sent an unusable response")
    if isinstance(error, StdioSessionRemoteError):
        return McpRemoteError("the MCP server returned an error response")
    return McpUnavailable("the MCP server is unavailable")


def _credential_identity(values: tuple[str, ...], key: str | None) -> str:
    """A durable digest that never returns credential material itself."""
    import hashlib
    import hmac

    material = json.dumps(values, separators=(",", ":")).encode()
    return (
        hmac.new(key.encode(), material, hashlib.sha256).hexdigest()
        if key
        else hashlib.sha256(material).hexdigest()
    )


def _json_bytes(value: object) -> int:
    try:
        return len(json.dumps(value, separators=(",", ":")).encode())
    except (TypeError, ValueError, RecursionError) as exc:
        raise McpProtocolError("the MCP server returned malformed JSON") from exc


def _text_blocks(content: Sequence[dict[str, JsonValue]]) -> tuple[str, ...]:
    return tuple(
        cast(str, block["text"])
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    )


def _omitted_kinds(content: Sequence[dict[str, JsonValue]]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                cast(str, block.get("type")) if isinstance(block.get("type"), str) else "unknown"
                for block in content
                if block.get("type") != "text"
            }
        )
    )
