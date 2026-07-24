"""Application-owned brain protocol. A brain runtime (e.g. the Claude CLI
adapter in infrastructure) receives a bounded, deterministic request and
returns exactly one proposed action. It never executes tools, never mutates
lifecycle state, and never sees vendor types — Friday validates, authorizes,
executes, and persists every side effect itself."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from friday.application.runtime_actions import BrainAction
from friday.application.tool_gateway import ToolDescriptor
from friday.domain.identifiers import RunId, TaskId
from friday.domain.json_value import JsonValue, ensure_json_value


@dataclass(frozen=True, slots=True)
class BrainRequest:
    """One bounded turn request: identity, the rendered context document,
    the allowed tool manifest, and response-size constraints."""

    run_id: RunId
    task_id: TaskId
    turn_number: int
    attempt_number: int
    context: str
    tool_manifest: tuple[ToolDescriptor, ...]
    max_response_bytes: int

    def __post_init__(self) -> None:
        if self.turn_number < 1:
            raise ValueError("turn_number must be >= 1")
        if self.attempt_number < 1:
            raise ValueError("attempt_number must be >= 1")
        if not self.context.strip():
            raise ValueError("context must not be empty")
        if self.max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")


@dataclass(frozen=True, slots=True)
class BrainResponse:
    """A validated proposed action plus persistence-safe model metadata.
    `repaired` records that the adapter used its one bounded correction
    attempt to obtain a schema-valid action."""

    action: BrainAction
    model: str | None = None
    usage: JsonValue = None
    repaired: bool = False

    def __post_init__(self) -> None:
        ensure_json_value(self.usage, path="$.usage")


class BrainRuntime(Protocol):
    def next_action(self, request: BrainRequest) -> BrainResponse: ...
