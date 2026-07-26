"""Semantic pointer operations: fresh descriptors, never snapshot ids or pixels."""

from __future__ import annotations

from pathlib import Path

import pytest

from friday.application.tool_gateway import ToolCall
from friday.infrastructure.computer.models import MAX_CLICK_COUNT, ElementTarget, PointerButton
from tests.infrastructure.computer_harness import (
    Harness,
    build_harness,
    element_input,
    failure_code,
    identity,
    output_of,
)


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return build_harness(tmp_path)


@pytest.mark.parametrize("tool", ("computer.click", "computer.scroll"))
def test_pointer_tools_require_approval(harness: Harness, tool: str) -> None:
    assessment = harness.gateway.assess(ToolCall(tool=tool, tool_input={}))

    assert assessment.read_only is False
    assert assessment.approval_required is True


def test_click_resolves_the_current_element_index_from_its_descriptor(harness: Harness) -> None:
    result = harness.run("computer.click", element_input())

    target = harness.driver.only_call("click").argument("target")
    assert isinstance(target, ElementTarget)
    assert target.element_index == 15
    assert target.element_token == "tok-send"
    assert output_of(result)["target"] == {
        "kind": "element",
        "role": "button",
        "label": "Send",
        "element_index": 15,
    }


@pytest.mark.parametrize("button", ["left", "right", "middle"])
def test_click_accepts_the_closed_button_set(harness: Harness, button: str) -> None:
    result = harness.run("computer.click", element_input(button=button))

    assert result.status == "succeeded"
    assert harness.driver.only_call("click").argument("button") is PointerButton(button)


@pytest.mark.parametrize("count", [1, 2])
def test_click_accepts_single_and_double_clicks(harness: Harness, count: int) -> None:
    result = harness.run("computer.click", element_input(count=count))

    assert result.status == "succeeded"
    assert harness.driver.only_call("click").argument("count") == count


def test_click_ceiling_is_a_double_click() -> None:
    assert MAX_CLICK_COUNT == 2


@pytest.mark.parametrize("coordinate_input", ({"x": 400, "y": 300}, {"x": 400}, {"y": 300}))
def test_pixel_targets_are_refused_before_any_mutation(
    harness: Harness, coordinate_input: dict[str, int]
) -> None:
    result = harness.run("computer.click", {**identity(), **coordinate_input})

    assert failure_code(result) == "tool_invalid_input"
    assert harness.driver.mutating_calls == ()


def test_targeted_scroll_uses_a_semantic_element(harness: Harness) -> None:
    result = harness.run("computer.scroll", element_input(direction="down", amount=2, by="line"))

    target = harness.driver.only_call("scroll").argument("target")
    assert isinstance(target, ElementTarget)
    assert target.element_index == 15
    assert output_of(result)["amount"] == 2


def test_window_scroll_remains_explicit_and_is_not_a_pixel_fallback(harness: Harness) -> None:
    result = harness.run("computer.scroll", {**identity(), "direction": "down"})

    assert result.status == "succeeded"
    assert output_of(result)["target"] == {"kind": "window"}
