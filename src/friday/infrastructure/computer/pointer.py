"""Pointer primitives: `computer.pointer_move`, `computer.click`, `computer.scroll`.

All three are mutating, all three require approval, and all three reach the
driver only after `resolve_fence` and `resolve_pointer_target` have proven the
target lies inside a window Friday observed within the TTL, under this Run.

Two bounds here are policy rather than representation:

* **Click count is 1 or 2.** A triple-click selects a paragraph and a
  ten-click is a stress test; both are distinct interactions that should be
  proposed and approved on their own terms, not reached by incrementing a
  number in an already-approved action.
* **Scroll delta has an operational ceiling.** `ScrollDelta` alone would allow
  ±100000, which is the representable limit, not a sane one. A runtime ceiling
  in the low thousands keeps a scroll a scroll instead of a fling to the end of
  an unbounded feed.

Each result reports the resolved absolute point. That is what makes the durable
ToolInvocation answer "where did Friday actually click?" long after the
snapshot it was fenced against has expired.
"""

from __future__ import annotations

from dataclasses import dataclass

from friday.application.errors import ToolInputInvalid
from friday.application.tool_gateway import ToolExecutionResult
from friday.domain.json_value import JsonValue
from friday.infrastructure.computer.context import ComputerToolContext
from friday.infrastructure.computer.driver import ComputerDriver
from friday.infrastructure.computer.models import (
    MAX_CLICK_COUNT,
    MIN_CLICK_COUNT,
    PointerButton,
    PointerTarget,
    ScrollDelta,
)
from friday.infrastructure.computer.snapshots import SnapshotRegistry
from friday.infrastructure.computer.targets import (
    FENCE_FIELDS,
    TARGET_FIELDS,
    resolve_fence,
    resolve_pointer_target,
)
from friday.infrastructure.computer.tool_input import (
    bounded_signed_int,
    optional_bounded_int,
    parse_object,
)

DEFAULT_MAX_SCROLL_DELTA = 5_000

_POINTER_MOVE_FIELDS = FENCE_FIELDS | TARGET_FIELDS
_CLICK_FIELDS = _POINTER_MOVE_FIELDS | {"button", "count"}
_SCROLL_FIELDS = _POINTER_MOVE_FIELDS | {"dx", "dy"}


@dataclass(frozen=True, slots=True)
class ComputerPointerSettings:
    max_scroll_delta: int = DEFAULT_MAX_SCROLL_DELTA

    def __post_init__(self) -> None:
        if self.max_scroll_delta < 1:
            raise ValueError("ComputerPointerSettings.max_scroll_delta must be positive")


class ComputerPointer:
    def __init__(
        self,
        driver: ComputerDriver,
        registry: SnapshotRegistry,
        settings: ComputerPointerSettings | None = None,
    ) -> None:
        self._driver = driver
        self._registry = registry
        self._settings = settings or ComputerPointerSettings()

    def pointer_move(
        self, tool_input: JsonValue, context: ComputerToolContext
    ) -> ToolExecutionResult:
        target = self._fenced_target(tool_input, context, allowed=_POINTER_MOVE_FIELDS)
        self._driver.move_pointer(target)
        return _target_result(target)

    def click(self, tool_input: JsonValue, context: ComputerToolContext) -> ToolExecutionResult:
        values = parse_object(tool_input, allowed=_CLICK_FIELDS)
        button = _button(values)
        count = optional_bounded_int(
            values, "count", maximum=MAX_CLICK_COUNT, default=MIN_CLICK_COUNT
        )
        target = self._resolve(values, context)
        self._driver.click(target, button=button, count=count)
        return _target_result(target, button=button.value, count=count)

    def scroll(self, tool_input: JsonValue, context: ComputerToolContext) -> ToolExecutionResult:
        values = parse_object(tool_input, allowed=_SCROLL_FIELDS)
        ceiling = self._settings.max_scroll_delta
        dx = bounded_signed_int(values, "dx", maximum=ceiling)
        dy = bounded_signed_int(values, "dy", maximum=ceiling)
        if dx == 0 and dy == 0:
            raise ToolInputInvalid("at least one of 'dx' or 'dy' must be non-zero")
        target = self._resolve(values, context)
        self._driver.scroll(target, delta=ScrollDelta(dx=dx, dy=dy))
        return _target_result(target, dx=dx, dy=dy)

    def _fenced_target(
        self,
        tool_input: JsonValue,
        context: ComputerToolContext,
        *,
        allowed: frozenset[str] | set[str],
    ) -> PointerTarget:
        values = parse_object(tool_input, allowed=frozenset(allowed))
        return self._resolve(values, context)

    def _resolve(self, values: dict[str, JsonValue], context: ComputerToolContext) -> PointerTarget:
        snapshot = resolve_fence(
            values, registry=self._registry, run_scope=context.run_scope, now=context.now
        )
        return resolve_pointer_target(snapshot, values)


def _button(values: dict[str, JsonValue]) -> PointerButton:
    """A closed enum, not a passthrough: an unrecognized button name must be a
    refusal, never something the driver gets to interpret."""
    raw = values.get("button", PointerButton.LEFT.value)
    if not isinstance(raw, str):
        raise ToolInputInvalid("'button' must be a string")
    try:
        return PointerButton(raw.strip().lower())
    except ValueError:
        allowed = sorted(button.value for button in PointerButton)
        raise ToolInputInvalid(f"'button' must be one of {allowed}") from None


def _target_result(target: PointerTarget, **extra: JsonValue) -> ToolExecutionResult:
    output: dict[str, JsonValue] = {
        "window_id": target.window_id,
        "x": target.point.x,
        "y": target.point.y,
    }
    output.update(extra)
    return ToolExecutionResult.succeeded(output)
