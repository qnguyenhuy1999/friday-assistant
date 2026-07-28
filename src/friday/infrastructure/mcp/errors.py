"""Stable MCP runtime failures and startup configuration errors."""

from __future__ import annotations


class McpError(Exception):
    code = "mcp_unavailable"


class McpUnavailable(McpError):
    code = "mcp_unavailable"


class McpConnectTimeout(McpError):
    code = "mcp_connect_timeout"


class McpCallTimeout(McpError):
    code = "mcp_call_timeout"


class McpProtocolError(McpError):
    code = "mcp_protocol_error"


class McpInvalidOutput(McpError):
    code = "mcp_invalid_output"


class McpRemoteError(McpError):
    code = "mcp_remote_error"


class McpConfigInvalid(ValueError):
    """Operator configuration that must stop startup."""
