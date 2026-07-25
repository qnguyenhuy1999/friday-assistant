"""Read-only computer-use handlers.

These are the tools Claude may call without approval, which is exactly why
they are kept apart from anything that can move a pointer: this module has no
path to a mutating driver method at all. It observes, bounds what it saw, and
returns it.

Output is bounded rather than truncated silently — `window_list` reports
`truncated` so Claude knows its view is partial instead of concluding a window
does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass

from friday.application.tool_gateway import ToolExecutionResult
from friday.domain.json_value import JsonValue
from friday.infrastructure.computer.context import ComputerToolContext
from friday.infrastructure.computer.driver import ComputerDriver
from friday.infrastructure.computer.json_shapes import window_json
from friday.infrastructure.computer.tool_input import (
    NO_FIELDS,
    optional_bounded_int,
    parse_object,
)


@dataclass(frozen=True, slots=True)
class ComputerObservationSettings:
    max_windows: int

    def __post_init__(self) -> None:
        if self.max_windows < 1:
            raise ValueError("ComputerObservationSettings.max_windows must be positive")


class ComputerObservation:
    def __init__(self, driver: ComputerDriver, settings: ComputerObservationSettings) -> None:
        self._driver = driver
        self._settings = settings

    def pointer_position(
        self, tool_input: JsonValue, context: ComputerToolContext
    ) -> ToolExecutionResult:
        del context  # observation is neither run-scoped nor snapshot-fenced
        parse_object(tool_input, allowed=NO_FIELDS)
        point = self._driver.pointer_position()
        return ToolExecutionResult.succeeded({"x": point.x, "y": point.y})

    def window_list(
        self, tool_input: JsonValue, context: ComputerToolContext
    ) -> ToolExecutionResult:
        del context
        values = parse_object(tool_input, allowed=frozenset({"limit"}))
        limit = optional_bounded_int(values, "limit", maximum=self._settings.max_windows)
        windows = self._driver.list_windows()
        shown: list[JsonValue] = [window_json(window) for window in windows[:limit]]
        return ToolExecutionResult.succeeded(
            {"windows": shown, "truncated": len(windows) > len(shown)}
        )

    def active_window(
        self, tool_input: JsonValue, context: ComputerToolContext
    ) -> ToolExecutionResult:
        del context
        parse_object(tool_input, allowed=NO_FIELDS)
        window = self._driver.active_window()
        return ToolExecutionResult.succeeded(
            {"window": window_json(window) if window is not None else None}
        )
