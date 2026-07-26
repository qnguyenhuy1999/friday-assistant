"""Pointer primitives: `computer.click` and `computer.scroll`.

Both are mutating, both require approval, and both reach the driver only after
TargetResolver has captured the named window and found the approved target in
what it just saw.

There is no `pointer_move`. Pointer motion without a click has no faithful
backing — the driver's cursor move is an overlay in window scope, and the real
OS pointer only moves in desktop scope, a coordinate space Friday never
captures and therefore cannot fence — and nothing needs it: click and scroll
address their target directly. A tool that appeared to move the pointer while
moving only an overlay would be a capability in the manifest that does not
exist on the desktop.

Two bounds here are policy rather than representation:

* **Click count is 1 or 2.** A triple-click selects a paragraph and a ten-click
  is a stress test; both are distinct interactions that should be proposed and
  approved on their own terms, not reached by incrementing a number in an
  already-approved action.
* **Scroll amount has an operational ceiling.** The model allows up to
  MAX_SCROLL_AMOUNT notches; the configured runtime ceiling is expected to be
  lower still, keeping a scroll a scroll rather than a fling to the end of an
  unbounded feed.

A scroll may omit its target. That is the driver's own distinction, not a
loosened fence: with a target it synthesizes a wheel event at that point, which
is the only way to reach a nested scrollable region; without one it drives the
window's focused scroller by keystroke. Both are legitimate, and forcing a
spurious element for the second would be a worse fence than naming it.
"""

from __future__ import annotations

from dataclasses import dataclass

from friday.application.tool_gateway import ToolExecutionResult
from friday.domain.json_value import JsonValue
from friday.infrastructure.computer.context import ComputerToolContext
from friday.infrastructure.computer.driver import ComputerDriver
from friday.infrastructure.computer.json_shapes import action_json, driver_effect_json
from friday.infrastructure.computer.models import (
    DEFAULT_SCROLL_AMOUNT,
    MAX_CLICK_COUNT,
    MAX_SCROLL_AMOUNT,
    MIN_CLICK_COUNT,
    PointerButton,
    ScrollCommand,
    ScrollDirection,
    ScrollGranularity,
)
from friday.infrastructure.computer.targets import (
    IDENTITY_FIELDS,
    TARGET_FIELDS,
    TargetResolver,
)
from friday.infrastructure.computer.tool_input import (
    enum_value,
    optional_bounded_int,
    parse_object,
)

DEFAULT_MAX_SCROLL_AMOUNT = 10
"""Runtime ceiling on notches per scroll. Below the model's MAX_SCROLL_AMOUNT
on purpose: the model bound stops an absurd value being representable, this one
is the operator's actual budget."""

_ADDRESSED_FIELDS = IDENTITY_FIELDS | TARGET_FIELDS
_CLICK_FIELDS = _ADDRESSED_FIELDS | {"button", "count"}
_SCROLL_FIELDS = _ADDRESSED_FIELDS | {"direction", "amount", "by"}

_BUTTONS = tuple(button.value for button in PointerButton)
_DIRECTIONS = tuple(direction.value for direction in ScrollDirection)
_GRANULARITIES = tuple(granularity.value for granularity in ScrollGranularity)


@dataclass(frozen=True, slots=True)
class ComputerPointerSettings:
    max_scroll_amount: int = DEFAULT_MAX_SCROLL_AMOUNT

    def __post_init__(self) -> None:
        if self.max_scroll_amount < 1 or self.max_scroll_amount > MAX_SCROLL_AMOUNT:
            raise ValueError(
                "ComputerPointerSettings.max_scroll_amount must be between 1 "
                f"and {MAX_SCROLL_AMOUNT}"
            )


class ComputerPointer:
    def __init__(
        self,
        driver: ComputerDriver,
        resolver: TargetResolver,
        settings: ComputerPointerSettings | None = None,
    ) -> None:
        self._driver = driver
        self._resolver = resolver
        self._settings = settings or ComputerPointerSettings()

    def click(self, tool_input: JsonValue, context: ComputerToolContext) -> ToolExecutionResult:
        values = parse_object(tool_input, allowed=_CLICK_FIELDS)
        button = PointerButton(enum_value(values, "button", allowed=_BUTTONS, default="left"))
        count = optional_bounded_int(
            values, "count", maximum=MAX_CLICK_COUNT, default=MIN_CLICK_COUNT
        )
        snapshot, target = self._resolver.resolve_addressed(values, now=context.now)
        result = self._driver.click(target, button=button, count=count)
        return ToolExecutionResult.succeeded(
            action_json(
                snapshot,
                target,
                button=button.value,
                count=count,
                **driver_effect_json(result.effect, result.verified),
            )
        )

    def scroll(self, tool_input: JsonValue, context: ComputerToolContext) -> ToolExecutionResult:
        values = parse_object(tool_input, allowed=_SCROLL_FIELDS)
        command = ScrollCommand(
            direction=ScrollDirection(enum_value(values, "direction", allowed=_DIRECTIONS)),
            amount=optional_bounded_int(
                values,
                "amount",
                maximum=self._settings.max_scroll_amount,
                # the driver's own default, clamped in case the operator
                # configured a ceiling below it — never the ceiling itself,
                # which would turn an omitted field into the largest scroll
                # allowed
                default=min(DEFAULT_SCROLL_AMOUNT, self._settings.max_scroll_amount),
            ),
            by=ScrollGranularity(enum_value(values, "by", allowed=_GRANULARITIES, default="line")),
        )
        snapshot, target = self._resolver.resolve_any(values, now=context.now)
        result = self._driver.scroll(target, command=command)
        return ToolExecutionResult.succeeded(
            action_json(
                snapshot,
                target,
                direction=command.direction.value,
                amount=command.amount,
                by=command.by.value,
                **driver_effect_json(result.effect, result.verified),
            )
        )
