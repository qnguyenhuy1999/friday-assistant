"""Fresh semantic target resolution for delayed computer mutations."""

from __future__ import annotations

from pathlib import Path

import pytest

from friday.infrastructure.computer.models import CapturedElement, ElementTarget, PixelFrame
from tests.infrastructure.computer_fakes import mail_ref, window_target
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


def test_changed_element_index_is_resolved_from_the_execution_capture(harness: Harness) -> None:
    """Worker B owns no state from Worker A; only the descriptor survives."""
    harness.capture()
    harness.driver.index_offset = 100

    result = harness.run("computer.click", element_input())

    target = harness.driver.only_call("click").argument("target")
    assert isinstance(target, ElementTarget)
    assert target.element_index == 115
    output_target = output_of(result)["target"]
    assert isinstance(output_target, dict)
    assert output_target["element_index"] == 115


@pytest.mark.parametrize(
    "elements, expected_code",
    (
        ((), "computer_target_not_found"),
        (
            (
                CapturedElement(
                    element_index=20,
                    role="button",
                    frame=PixelFrame(x=0, y=0, width=10, height=10),
                    label="Send",
                ),
                CapturedElement(
                    element_index=21,
                    role="button",
                    frame=PixelFrame(x=20, y=0, width=10, height=10),
                    label="Send",
                ),
            ),
            "computer_target_ambiguous",
        ),
        (
            (
                CapturedElement(
                    element_index=20,
                    role="button",
                    frame=PixelFrame(x=0, y=0, width=10, height=10),
                    label="Discard",
                ),
            ),
            "computer_target_not_found",
        ),
        (
            (
                CapturedElement(
                    element_index=20,
                    role="link",
                    frame=PixelFrame(x=0, y=0, width=10, height=10),
                    label="Send",
                ),
            ),
            "computer_target_not_found",
        ),
    ),
)
def test_descriptor_divergence_fails_closed(
    harness: Harness, elements: tuple[CapturedElement, ...], expected_code: str
) -> None:
    harness.driver.elements = elements

    result = harness.run("computer.click", element_input())

    assert failure_code(result) == expected_code
    assert harness.driver.mutating_calls == ()


def test_different_pid_or_window_cannot_reuse_the_same_semantic_approval(harness: Harness) -> None:
    for changed_identity in (
        {"pid": 999, "window_id": 10725},
        {"pid": 844, "window_id": 20880},
    ):
        result = harness.run("computer.click", {**element_input(), **changed_identity})

        assert failure_code(result) == "computer_window_gone"
    assert harness.driver.mutating_calls == ()


def test_semantic_resolution_reuses_no_capture_image_or_snapshot_state(harness: Harness) -> None:
    harness.run("computer.click", element_input())

    assert harness.driver.only_call("capture").argument("include_screenshot") is False


def test_window_target_is_explicit_only_for_window_wide_operations(harness: Harness) -> None:
    result = harness.run("computer.scroll", {**identity(), "direction": "down"})

    assert harness.driver.only_call("scroll").argument("target") == window_target()
    assert output_of(result)["target"] == {"kind": "window"}


def test_bring_to_front_addresses_exactly_the_named_window(harness: Harness) -> None:
    result = harness.run("computer.bring_to_front", identity())

    assert harness.driver.only_call("bring_to_front").argument("ref") == mail_ref()
    assert output_of(result)["target"] == {"kind": "window"}
