"""`computer.focus_window` — raising a window Friday has actually seen.

Focusing looks harmless next to clicking, and it is not: it changes which
application receives every subsequent keystroke. A `type_text` fenced against
one window, executed just after focus moved to another, is how a message
intended for a notes app ends up somewhere else. So it is mutating, it requires
approval, and it requires the same snapshot fence as everything else.

The window must come from the cited capture. There is no path here for an
arbitrary window id Claude composed or remembered from an earlier, expired
capture — `resolve_fence` refuses both, and the id handed to the driver is the
one *the snapshot* recorded, not the one the tool input supplied.
"""

from __future__ import annotations

from friday.application.tool_gateway import ToolExecutionResult
from friday.domain.json_value import JsonValue
from friday.infrastructure.computer.context import ComputerToolContext
from friday.infrastructure.computer.driver import ComputerDriver
from friday.infrastructure.computer.snapshots import SnapshotRegistry
from friday.infrastructure.computer.targets import FENCE_FIELDS, resolve_fence
from friday.infrastructure.computer.tool_input import parse_object


class ComputerWindows:
    def __init__(self, driver: ComputerDriver, registry: SnapshotRegistry) -> None:
        self._driver = driver
        self._registry = registry

    def focus_window(
        self, tool_input: JsonValue, context: ComputerToolContext
    ) -> ToolExecutionResult:
        values = parse_object(tool_input, allowed=FENCE_FIELDS)
        snapshot = resolve_fence(
            values, registry=self._registry, run_scope=context.run_scope, now=context.now
        )
        window_id = snapshot.window.window_id
        self._driver.focus_window(window_id)
        return ToolExecutionResult.succeeded({"window_id": window_id})
