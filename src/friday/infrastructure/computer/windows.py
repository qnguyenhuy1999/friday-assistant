"""`computer.bring_to_front` — raising a window to the foreground.

Named for its actual effect. The underlying driver operation genuinely steals
foreground from whatever the user was working in; it is not focus bookkeeping,
and the driver documents it as an explicit opt-in that the ordinary input path
never needs (background dispatch reaches backgrounded windows). A tool called
`focus_window` would have implied something weaker and more routine than what
happens, to Claude and to whoever is reading the approval.

It is mutating and it requires approval for the same reason: it changes which
application receives every subsequent keystroke. A `type_text` approved against
one window, executed just after the foreground moved to another, is how a
message intended for a notes app ends up somewhere else.

The window is captured before it is raised. Raising a window that no longer
exists would be harmless but untruthful, and the capture is what lets the
result name the window that was actually brought forward.
"""

from __future__ import annotations

from friday.application.tool_gateway import ToolExecutionResult
from friday.domain.json_value import JsonValue
from friday.infrastructure.computer.context import ComputerToolContext
from friday.infrastructure.computer.driver import ComputerDriver
from friday.infrastructure.computer.json_shapes import action_json, driver_effect_json
from friday.infrastructure.computer.models import WindowTarget
from friday.infrastructure.computer.targets import IDENTITY_FIELDS, TargetResolver
from friday.infrastructure.computer.tool_input import parse_object


class ComputerWindows:
    def __init__(self, driver: ComputerDriver, resolver: TargetResolver) -> None:
        self._driver = driver
        self._resolver = resolver

    def bring_to_front(
        self, tool_input: JsonValue, context: ComputerToolContext
    ) -> ToolExecutionResult:
        values = parse_object(tool_input, allowed=IDENTITY_FIELDS)
        snapshot = self._resolver.resolve_window(values, now=context.now)
        result = self._driver.bring_to_front(snapshot.window.ref)
        return ToolExecutionResult.succeeded(
            action_json(
                snapshot,
                WindowTarget(ref=snapshot.window.ref),
                **driver_effect_json(result.effect, result.verified),
            )
        )
