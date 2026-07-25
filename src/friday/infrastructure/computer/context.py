"""What a computer-use handler knows about the execution it is part of.

Three facts, and deliberately no more. A handler needs to scope snapshots to a
Run, name an artifact after its invocation, and evaluate a TTL — it does not
need the Run aggregate, the step, the claim, or a Unit of Work, and giving it
any of those would invite a handler to make a durable decision that belongs to
ExecuteToolAction.

Identities arrive as strings rather than as `RunId`/`ToolInvocationId`. The
gateway stringifies them at the boundary, which keeps every value object in
this package free of domain identifier types and keeps the snapshot registry
comparing opaque scopes rather than reimplementing identity semantics.

`now` is passed in rather than read from a clock so that TTL evaluation is a
pure function of its inputs: a snapshot's expiry is decided by the same
timestamp the caller used, not by whatever the wall clock said a few
microseconds later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from friday.application.tool_gateway import ToolExecutionResult
from friday.domain.json_value import JsonValue


@dataclass(frozen=True, slots=True)
class ComputerToolContext:
    run_scope: str
    invocation_id: str
    now: datetime

    def __post_init__(self) -> None:
        if not self.run_scope.strip():
            raise ValueError("ComputerToolContext.run_scope must not be empty")
        if not self.invocation_id.strip():
            raise ValueError("ComputerToolContext.invocation_id must not be empty")
        if self.now.tzinfo is None:
            raise ValueError("ComputerToolContext.now must be timezone-aware")
        object.__setattr__(self, "now", self.now.astimezone(UTC))


class ComputerToolHandler(Protocol):
    def __call__(
        self, tool_input: JsonValue, context: ComputerToolContext
    ) -> ToolExecutionResult: ...
