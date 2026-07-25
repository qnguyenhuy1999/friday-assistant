"""Pointer primitives: resolution, bounds, and the closed enums around a click.

The resolution assertions check the *point that reached the driver*, not just
that the call succeeded. An element resolved to the wrong pixel still returns
"succeeded", and the difference between clicking Send and clicking Delete is
sometimes thirty pixels.

Click count and button are tested as closed sets rather than by example, because
the failure mode is a value passing through to the driver uninterpreted — and a
test that only tried valid values would never notice.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from friday.application.tool_gateway import ToolCall
from friday.infrastructure.computer.models import (
    MAX_CLICK_COUNT,
    PointerButton,
    ScreenPoint,
    ScrollDelta,
)
from tests.infrastructure.computer_harness import (
    Harness,
    build_harness,
    failure_code,
    output_of,
)

MUTATING_POINTER_TOOLS = ("computer.pointer_move", "computer.click", "computer.scroll")


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return build_harness(tmp_path)


# --- approval posture -----------------------------------------------------


@pytest.mark.parametrize("tool", MUTATING_POINTER_TOOLS)
def test_pointer_tools_require_approval(harness: Harness, tool: str) -> None:
    assessment = harness.gateway.assess(ToolCall(tool=tool, tool_input={}))

    assert assessment.read_only is False
    assert assessment.approval_required is True


# --- pointer_move ---------------------------------------------------------


def test_pointer_move_resolves_element_to_snapshot_target(harness: Harness) -> None:
    """Element 14 is the search field at (100,50) 200x30, so its centre is
    (200,65). The driver must receive that point, not the element id."""
    snapshot_id = harness.capture_snapshot()

    output = output_of(
        harness.run("computer.pointer_move", {**harness.fence(snapshot_id), "element": 14})
    )

    assert output == {"window_id": "win-mail", "x": 200, "y": 65}
    call = harness.driver.only_call("move_pointer")
    assert call.argument("point") == ScreenPoint(x=200, y=65)
    assert call.argument("window_id") == "win-mail"


def test_pointer_move_accepts_valid_coordinates(harness: Harness) -> None:
    snapshot_id = harness.capture_snapshot()

    output = output_of(
        harness.run("computer.pointer_move", {**harness.fence(snapshot_id), "x": 300, "y": 220})
    )

    assert output == {"window_id": "win-mail", "x": 300, "y": 220}
    assert harness.driver.only_call("move_pointer").argument("point") == ScreenPoint(x=300, y=220)


def test_pointer_move_rejects_out_of_bounds_coordinates(harness: Harness) -> None:
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()

    result = harness.run(
        "computer.pointer_move", {**harness.fence(snapshot_id), "x": 5000, "y": 220}
    )

    assert failure_code(result) == "computer_target_out_of_bounds"
    assert harness.driver.mutating_calls == ()


def test_pointer_move_rejects_unknown_fields(harness: Harness) -> None:
    snapshot_id = harness.capture_snapshot()

    result = harness.run(
        "computer.pointer_move", {**harness.fence(snapshot_id), "element": 14, "button": "left"}
    )

    assert failure_code(result) == "tool_invalid_input"
    assert harness.driver.mutating_calls == ()


# --- click ----------------------------------------------------------------


def test_click_defaults_to_left_single_click(harness: Harness) -> None:
    snapshot_id = harness.capture_snapshot()

    output = output_of(harness.run("computer.click", {**harness.fence(snapshot_id), "element": 15}))

    assert output == {"window_id": "win-mail", "x": 360, "y": 65, "button": "left", "count": 1}
    call = harness.driver.only_call("click")
    assert call.argument("button") is PointerButton.LEFT
    assert call.argument("count") == 1


@pytest.mark.parametrize("button", ["left", "right", "middle"])
def test_click_accepts_every_allowed_button(harness: Harness, button: str) -> None:
    snapshot_id = harness.capture_snapshot()

    result = harness.run(
        "computer.click", {**harness.fence(snapshot_id), "element": 14, "button": button}
    )

    assert result.status == "succeeded"
    assert harness.driver.only_call("click").argument("button") is PointerButton(button)


@pytest.mark.parametrize("button", ["double", "primary", "", "  ", 1, True, None, ["left"]])
def test_click_button_is_closed_enum(harness: Harness, button: object) -> None:
    """An unrecognized button must be a refusal, never driver passthrough."""
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()

    result = harness.run(
        "computer.click",
        {**harness.fence(snapshot_id), "element": 14, "button": button},  # type: ignore[dict-item]
    )

    assert failure_code(result) == "tool_invalid_input"
    assert harness.driver.mutating_calls == ()


def test_click_button_names_are_case_insensitive(harness: Harness) -> None:
    """Normalization happens before the enum lookup, so `RIGHT` is the same
    action as `right` — and therefore must reach the driver as the same value."""
    snapshot_id = harness.capture_snapshot()

    result = harness.run(
        "computer.click", {**harness.fence(snapshot_id), "element": 14, "button": "RIGHT"}
    )

    assert result.status == "succeeded"
    assert harness.driver.only_call("click").argument("button") is PointerButton.RIGHT


@pytest.mark.parametrize("count", [1, 2])
def test_click_accepts_single_and_double_clicks(harness: Harness, count: int) -> None:
    snapshot_id = harness.capture_snapshot()

    result = harness.run(
        "computer.click", {**harness.fence(snapshot_id), "element": 14, "count": count}
    )

    assert result.status == "succeeded"
    assert harness.driver.only_call("click").argument("count") == count


@pytest.mark.parametrize("count", [0, -1, 3, 10, 1000, True, 1.5, "2", None])
def test_click_count_is_bounded(harness: Harness, count: object) -> None:
    """A triple-click is a different interaction, not a bigger click."""
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()

    result = harness.run(
        "computer.click",
        {**harness.fence(snapshot_id), "element": 14, "count": count},  # type: ignore[dict-item]
    )

    assert failure_code(result) == "tool_invalid_input"
    assert harness.driver.mutating_calls == ()


def test_the_click_ceiling_is_a_double_click() -> None:
    assert MAX_CLICK_COUNT == 2


# --- scroll ---------------------------------------------------------------


def test_scroll_passes_the_delta_and_resolved_point(harness: Harness) -> None:
    snapshot_id = harness.capture_snapshot()

    output = output_of(
        harness.run("computer.scroll", {**harness.fence(snapshot_id), "element": 14, "dy": -300})
    )

    assert output == {"window_id": "win-mail", "x": 200, "y": 65, "dx": 0, "dy": -300}
    delta = harness.driver.only_call("scroll").argument("delta")
    assert isinstance(delta, ScrollDelta)
    assert (delta.dx, delta.dy) == (0, -300)


@pytest.mark.parametrize("delta", [{}, {"dx": 0}, {"dy": 0}, {"dx": 0, "dy": 0}])
def test_scroll_requires_nonzero_delta(harness: Harness, delta: dict[str, int]) -> None:
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()

    result = harness.run("computer.scroll", {**harness.fence(snapshot_id), "element": 14, **delta})

    assert failure_code(result) == "tool_invalid_input"
    assert harness.driver.mutating_calls == ()


def test_scroll_delta_respects_runtime_ceiling(tmp_path: Path) -> None:
    """The operational ceiling, not the representable one: `ScrollDelta` alone
    would happily accept a fling of 100000."""
    harness = build_harness(tmp_path, max_scroll_delta=100)
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()

    rejected = harness.run(
        "computer.scroll", {**harness.fence(snapshot_id), "element": 14, "dy": -101}
    )
    accepted = harness.run(
        "computer.scroll", {**harness.fence(snapshot_id), "element": 14, "dy": -100}
    )

    assert failure_code(rejected) == "tool_invalid_input"
    assert accepted.status == "succeeded"
    assert len(harness.driver.mutating_calls) == 1


def test_scroll_ceiling_is_far_below_the_representable_range(tmp_path: Path) -> None:
    """Guards against the ceiling silently becoming MAX_COORDINATE again."""
    harness = build_harness(tmp_path)
    snapshot_id = harness.capture_snapshot()

    result = harness.run(
        "computer.scroll", {**harness.fence(snapshot_id), "element": 14, "dy": 99_999}
    )

    assert failure_code(result) == "tool_invalid_input"
