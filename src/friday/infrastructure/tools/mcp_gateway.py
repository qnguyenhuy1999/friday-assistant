"""Friday-owned MCP policy, execution, failure translation, and lifecycle."""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from friday.application.errors import ToolExecutionAmbiguous, ToolInputInvalid, ToolNotFound
from friday.application.ports import Clock
from friday.application.tool_gateway import (
    ToolCall,
    ToolDescriptor,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolRiskAssessment,
)
from friday.domain.failure import Failure, FailureCause
from friday.infrastructure.clock import SystemClock
from friday.infrastructure.mcp.bindings import McpBindingRegistry, McpBoundTool
from friday.infrastructure.mcp.client import McpClient
from friday.infrastructure.mcp.config import McpServerConfig
from friday.infrastructure.mcp.discovery import McpServerDiscovery
from friday.infrastructure.mcp.errors import McpError, McpRemoteError
from friday.infrastructure.mcp.health import McpServerHealth, McpServerStatus
from friday.infrastructure.mcp.output import OutputBounds, normalize_call_result
from friday.infrastructure.mcp.schema import validate_input

_FAILURE_MESSAGES = {
    "mcp_unavailable": "the external MCP integration is unavailable",
    "mcp_connect_timeout": "the external MCP integration did not connect in time",
    "mcp_call_timeout": "the external MCP operation did not complete in time",
    "mcp_protocol_error": "the external MCP integration returned an unusable response",
    "mcp_invalid_output": "the external MCP operation returned an unusable result",
    "mcp_remote_error": "the external service reported an error",
}
_FAILURE_CAUSES = {
    "mcp_unavailable": FailureCause.TOOL,
    "mcp_connect_timeout": FailureCause.TIMEOUT,
    "mcp_call_timeout": FailureCause.TIMEOUT,
    "mcp_protocol_error": FailureCause.TOOL,
    "mcp_invalid_output": FailureCause.TOOL,
    "mcp_remote_error": FailureCause.TOOL,
}


@dataclass(frozen=True, slots=True)
class McpServerStack:
    config: McpServerConfig
    client: McpClient
    discovery: McpServerDiscovery


@dataclass(slots=True)
class _ServerState:
    stack: McpServerStack
    last_success: datetime | None = None
    failure_code: str | None = None


class McpToolGateway:
    def __init__(self, stacks: Sequence[McpServerStack], *, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()
        self._states = [_ServerState(stack=stack) for stack in stacks]
        available = [tool for state in self._states for tool in state.stack.discovery.available]
        self._registry = McpBindingRegistry(available)
        self._owners = {
            tool.local_name: state
            for state in self._states
            for tool in state.stack.discovery.available
        }
        self._descriptors = tuple(self._descriptor(name) for name in self._registry.local_names())
        self._closed = False

    def list_tools(self) -> tuple[ToolDescriptor, ...]:
        return self._descriptors

    def assess(self, call: ToolCall) -> ToolRiskAssessment:
        tool = self._require_bound(call.tool)
        return ToolRiskAssessment(
            tool=call.tool,
            read_only=tool.binding.read_only,
            approval_required=tool.binding.approval_required,
            category=tool.binding.approval_category,
            summary=tool.approval_summary,
            authorization_scope=tool.authorization_scope,
            provenance=tool.provenance,
        )

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        tool_name = request.call.tool
        if not self._is_configured(tool_name):
            raise ToolNotFound(tool_name)
        tool = self._registry.get(tool_name)
        if tool is None:
            return self._failure("mcp_unavailable", read_only=True)
        state = self._owners[tool_name]
        if request.cancellation_requested is not None and request.cancellation_requested():
            return ToolExecutionResult.failed(
                Failure(
                    code="claim_lost",
                    message="claim lost before the external MCP call",
                    retryable=True,
                    cause=FailureCause.CANCELLED,
                )
            )
        try:
            validate_input(tool.normalized_schema, request.call.tool_input)
        except ToolInputInvalid as exc:
            return ToolExecutionResult.failed(
                Failure(
                    code="tool_invalid_input",
                    message=str(exc),
                    retryable=False,
                    cause=FailureCause.VALIDATION,
                )
            )
        timeout = self._effective_timeout(request.timeout_seconds, state.stack.config)
        if timeout <= 0:
            state.failure_code = "mcp_call_timeout"
            return self._failure("mcp_call_timeout", read_only=tool.binding.read_only)
        assert isinstance(request.call.tool_input, dict)
        try:
            raw = state.stack.client.call_tool(
                tool.remote_tool_name,
                dict(request.call.tool_input),
                timeout_seconds=timeout,
                cancelled=request.cancellation_requested,
            )
            output = normalize_call_result(raw, bounds=OutputBounds.from_server(state.stack.config))
        except McpError as exc:
            state.failure_code = exc.code
            # Once tools/call has been dispatched, a transport-level error is
            # not evidence that a mutating remote operation did not happen.
            # Leave its durable invocation RUNNING; the executor will refuse
            # blind replay and surface an ambiguous outcome.
            if not tool.binding.read_only and not isinstance(exc, McpRemoteError):
                raise ToolExecutionAmbiguous(
                    "the external mutating MCP operation may have completed; "
                    "refusing to record failure or retry it automatically"
                ) from exc
            return self._failure(exc.code, read_only=tool.binding.read_only)
        state.last_success = self._clock.now()
        state.failure_code = None
        return ToolExecutionResult.succeeded(output)

    def health(self) -> tuple[McpServerHealth, ...]:
        return tuple(
            McpServerHealth(
                server_id=state.stack.config.server_id,
                status=self._status(state),
                configured_binding_count=len(state.stack.config.bindings),
                available_binding_count=len(state.stack.discovery.available),
                last_success=state.last_success,
                failure_code=state.failure_code or state.stack.discovery.failure_code,
            )
            for state in self._states
        )

    @staticmethod
    def _status(state: _ServerState) -> McpServerStatus:
        """Startup discovery is a snapshot; a runtime failure is newer news.

        Reporting `available` next to a live `failure_code` tells an operator
        the server is fine while every call to it is failing, which is worse
        than saying nothing.
        """
        if state.failure_code is None:
            return state.stack.discovery.status
        return "unavailable" if not state.stack.discovery.available else "degraded"

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for state in self._states:
            with contextlib.suppress(Exception):
                state.stack.client.close()

    def _descriptor(self, local_name: str) -> ToolDescriptor:
        tool = self._registry.get(local_name)
        assert tool is not None
        return ToolDescriptor(
            name=local_name,
            description=tool.binding.trusted_description,
            read_only=tool.binding.read_only,
            approval_required=tool.binding.approval_required,
            input_schema=tool.normalized_schema,
        )

    def _require_bound(self, tool_name: str) -> McpBoundTool:
        tool = self._registry.get(tool_name)
        if tool is None:
            raise ToolNotFound(tool_name)
        return tool

    def _is_configured(self, tool_name: str) -> bool:
        return any(
            binding.local_name == tool_name
            for state in self._states
            for binding in state.stack.config.bindings
        )

    @staticmethod
    def _effective_timeout(requested: float | None, config: McpServerConfig) -> float:
        return (
            config.call_timeout_seconds
            if requested is None
            else min(requested, config.call_timeout_seconds)
        )

    @staticmethod
    def _failure(code: str, *, read_only: bool) -> ToolExecutionResult:
        return ToolExecutionResult.failed(
            Failure(
                code=code,
                message=_FAILURE_MESSAGES[code],
                retryable=read_only,
                cause=_FAILURE_CAUSES[code],
            )
        )
