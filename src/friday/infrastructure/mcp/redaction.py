"""Ephemeral redaction of values intentionally passed to an MCP child."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from friday.domain.json_value import JsonValue
from friday.infrastructure.mcp.errors import McpProtocolError

REDACTED = "[REDACTED]"


@dataclass(frozen=True, slots=True)
class SensitiveValueRedactor:
    values: tuple[str, ...]

    @classmethod
    def from_values(cls, values: Iterable[object]) -> SensitiveValueRedactor:
        # A short credential is still a credential. Values, not variable
        # names, are redacted; longest-first prevents partial replacement.
        return cls(
            tuple(sorted({v for v in values if isinstance(v, str) and v}, key=len, reverse=True))
        )

    def redact(self, value: JsonValue) -> JsonValue:
        try:
            return self._walk(value, 1)
        except RecursionError as exc:
            raise McpProtocolError("the MCP server returned excessively nested data") from exc

    def _walk(self, value: JsonValue, depth: int) -> JsonValue:
        if depth > 64:
            raise McpProtocolError("the MCP server returned excessively nested data")
        if isinstance(value, str):
            return self._text(value)
        if isinstance(value, list):
            return [self._walk(item, depth + 1) for item in value]
        if isinstance(value, dict):
            return {self._text(key): self._walk(item, depth + 1) for key, item in value.items()}
        return value

    def _text(self, value: str) -> str:
        for secret in self.values:
            value = value.replace(secret, REDACTED)
        return value
