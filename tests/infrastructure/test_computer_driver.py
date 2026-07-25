"""ComputerDriver port conformance and FakeComputerDriver behaviour.

The port has no runtime enforcement — it is a typing Protocol — so the
conformance check here is a typed assignment that mypy verifies statically.
The rest pins down the fake's observability contract, which every later
safety test depends on: if `mutating_calls` under-reports, a test asserting
"the driver was never called" would pass for the wrong reason.
"""

from __future__ import annotations

import pytest

from friday.infrastructure.computer.driver import ComputerDriver, ComputerDriverHealth
from friday.infrastructure.computer.errors import (
    ComputerDriverFailed,
    ComputerDriverTimeout,
    ComputerDriverUnavailable,
    ComputerUseError,
)
from friday.infrastructure.computer.models import (
    CaptureRequest,
    KeyModifier,
    KeyName,
    Keystroke,
    PointerButton,
    PointerTarget,
    ScreenPoint,
    ScrollDelta,
)
from tests.infrastructure.computer_fakes import (
    PNG_BYTES,
    FakeComputerDriver,
    default_window,
)


@pytest.fixture
def driver() -> FakeComputerDriver:
    return FakeComputerDriver()


def target(x: int = 150, y: int = 60, window_id: str | None = "win-mail") -> PointerTarget:
    return PointerTarget(point=ScreenPoint(x=x, y=y), window_id=window_id)


# --- port shape -----------------------------------------------------------


def test_fake_driver_satisfies_the_computer_driver_port(driver: FakeComputerDriver) -> None:
    """Typed assignment: mypy fails the build if the fake drifts from the
    port, so the fake can never quietly diverge from what adapters must
    implement."""
    port: ComputerDriver = driver

    assert port.health().available is True


def test_health_detail_defaults_to_empty() -> None:
    assert ComputerDriverHealth(available=False).detail == ""


def test_every_driver_error_is_a_computer_use_error() -> None:
    """The gateway catches ComputerUseError as its single mapping funnel, so
    no driver failure may sit outside that hierarchy."""
    for error_type in (
        ComputerDriverUnavailable,
        ComputerDriverTimeout,
        ComputerDriverFailed,
    ):
        assert issubclass(error_type, ComputerUseError)


# --- read-only observation ------------------------------------------------


def test_capture_returns_the_active_window_with_elements_and_screenshot(
    driver: FakeComputerDriver,
) -> None:
    result = driver.capture(CaptureRequest())

    assert result.window.window_id == "win-mail"
    assert [element.element_id for element in result.elements] == [14, 15]
    assert result.screenshot is not None
    assert result.screenshot.data == PNG_BYTES


def test_capture_can_target_a_specific_window(driver: FakeComputerDriver) -> None:
    driver.windows = (default_window(), default_window("win-notes", is_active=False))

    result = driver.capture(CaptureRequest(window_id="win-notes"))

    assert result.window.window_id == "win-notes"


def test_capture_of_an_unknown_window_fails(driver: FakeComputerDriver) -> None:
    with pytest.raises(ComputerDriverFailed, match="unknown window"):
        driver.capture(CaptureRequest(window_id="win-ghost"))


def test_capture_with_no_active_window_fails(driver: FakeComputerDriver) -> None:
    driver.windows = (default_window(is_active=False),)

    with pytest.raises(ComputerDriverFailed, match="no active window"):
        driver.capture(CaptureRequest())


def test_capture_honours_the_element_budget(driver: FakeComputerDriver) -> None:
    assert len(driver.capture(CaptureRequest(max_elements=1)).elements) == 1


def test_capture_can_omit_elements_and_screenshot(driver: FakeComputerDriver) -> None:
    result = driver.capture(CaptureRequest(include_elements=False, include_screenshot=False))

    assert result.elements == ()
    assert result.screenshot is None


def test_pointer_position_and_window_listing_are_observations(driver: FakeComputerDriver) -> None:
    assert driver.pointer_position() == ScreenPoint(x=0, y=0)
    assert driver.list_windows() == (default_window(),)
    active = driver.active_window()
    assert active is not None and active.window_id == "win-mail"


def test_active_window_is_none_when_nothing_is_focused(driver: FakeComputerDriver) -> None:
    driver.windows = (default_window(is_active=False),)

    assert driver.active_window() is None


def test_read_only_calls_are_not_counted_as_mutating(driver: FakeComputerDriver) -> None:
    driver.capture(CaptureRequest())
    driver.pointer_position()
    driver.list_windows()
    driver.active_window()

    assert driver.mutating_calls == ()
    assert driver.call_names == ("capture", "pointer_position", "list_windows", "active_window")


# --- mutating input -------------------------------------------------------


def test_move_pointer_records_the_exact_point_and_updates_position(
    driver: FakeComputerDriver,
) -> None:
    result = driver.move_pointer(target(x=200, y=90))

    call = driver.only_call("move_pointer")
    assert call.argument("point") == ScreenPoint(x=200, y=90)
    assert call.argument("window_id") == "win-mail"
    assert driver.pointer == ScreenPoint(x=200, y=90)
    assert result.pointer_position == ScreenPoint(x=200, y=90)


def test_click_records_button_and_count(driver: FakeComputerDriver) -> None:
    driver.click(target(), button=PointerButton.RIGHT, count=2)

    call = driver.only_call("click")
    assert call.argument("button") is PointerButton.RIGHT
    assert call.argument("count") == 2


def test_scroll_records_its_delta(driver: FakeComputerDriver) -> None:
    driver.scroll(target(), delta=ScrollDelta(dx=0, dy=-120))

    assert driver.only_call("scroll").argument("delta") == ScrollDelta(dx=0, dy=-120)


def test_type_text_records_text_and_target_window(driver: FakeComputerDriver) -> None:
    driver.type_text("hello", window_id="win-mail")

    call = driver.only_call("type_text")
    assert call.argument("text") == "hello"
    assert call.argument("window_id") == "win-mail"


def test_press_keystroke_records_the_canonical_combination(driver: FakeComputerDriver) -> None:
    driver.press_keystroke(
        Keystroke(key=KeyName.ENTER, modifiers=(KeyModifier.SHIFT,)), window_id=None
    )

    keystroke = driver.only_call("press_keystroke").argument("keystroke")
    assert isinstance(keystroke, Keystroke)
    assert keystroke.combination == "shift+enter"


def test_focus_window_moves_activation(driver: FakeComputerDriver) -> None:
    driver.windows = (default_window(), default_window("win-notes", is_active=False))

    driver.focus_window("win-notes")

    active = driver.active_window()
    assert active is not None and active.window_id == "win-notes"


def test_focus_window_rejects_an_unknown_window(driver: FakeComputerDriver) -> None:
    with pytest.raises(ComputerDriverFailed, match="unknown window"):
        driver.focus_window("win-ghost")


def test_every_mutating_call_is_recorded(driver: FakeComputerDriver) -> None:
    driver.move_pointer(target())
    driver.click(target(), button=PointerButton.LEFT, count=1)
    driver.scroll(target(), delta=ScrollDelta(dx=0, dy=3))
    driver.type_text("x", window_id=None)
    driver.press_keystroke(Keystroke(key="c", modifiers=(KeyModifier.META,)), window_id=None)
    driver.focus_window("win-mail")

    assert [call.name for call in driver.mutating_calls] == [
        "move_pointer",
        "click",
        "scroll",
        "type_text",
        "press_keystroke",
        "focus_window",
    ]


# --- failure scripting ----------------------------------------------------


def test_an_unavailable_driver_refuses_every_call(driver: FakeComputerDriver) -> None:
    driver.available = False

    with pytest.raises(ComputerDriverUnavailable):
        driver.capture(CaptureRequest())
    with pytest.raises(ComputerDriverUnavailable):
        driver.click(target(), button=PointerButton.LEFT, count=1)
    assert driver.calls == []


def test_health_reports_unavailability_without_raising(driver: FakeComputerDriver) -> None:
    driver.available = False
    driver.health_detail = "cua-driver not on PATH"

    health = driver.health()

    assert health.available is False
    assert health.detail == "cua-driver not on PATH"


def test_a_scripted_error_is_raised_instead_of_recording_a_call(
    driver: FakeComputerDriver,
) -> None:
    driver.raises = ComputerDriverTimeout("driver did not answer")

    with pytest.raises(ComputerDriverTimeout):
        driver.click(target(), button=PointerButton.LEFT, count=1)
    assert driver.calls == []


def test_only_call_rejects_ambiguous_expectations(driver: FakeComputerDriver) -> None:
    """Guards the assertion helper itself: two clicks must not read as one."""
    driver.click(target(), button=PointerButton.LEFT, count=1)
    driver.click(target(), button=PointerButton.LEFT, count=1)

    with pytest.raises(AssertionError, match="expected exactly one 'click' call, got 2"):
        driver.only_call("click")
    with pytest.raises(AssertionError, match="expected exactly one 'scroll' call, got 0"):
        driver.only_call("scroll")
