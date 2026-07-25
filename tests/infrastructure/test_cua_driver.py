"""CuaDriverComputerDriver against a fake MCP transport.

No real desktop and no real subprocess: the adapter's job is translation and
distrust, and both are fully observable at the transport seam. CI must never
depend on a machine having a window server.

Two properties carry most of the weight:

* **Every reply is validated.** A driver is untrusted infrastructure input, so
  a missing field, a wrong type, or a bool where an int belongs must raise
  rather than be coerced into something plausible.
* **Nothing from a failure escapes.** Transport errors, JSON-RPC error bodies,
  and stderr text can quote absolute paths, usernames, and window contents, so
  the messages that cross the boundary are constants.

The startup health check is tested as a *total* check: a driver missing one of
the ten tools is unavailable now, not working-until-the-first-click.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest

from friday.domain.json_value import JsonValue
from friday.infrastructure.computer.cua_driver import (
    CuaDriverComputerDriver,
    CuaDriverSettings,
    CuaToolNames,
)
from friday.infrastructure.computer.errors import (
    ComputerDriverFailed,
    ComputerDriverTimeout,
    ComputerDriverUnavailable,
)
from friday.infrastructure.computer.models import (
    CaptureRequest,
    KeyModifier,
    Keystroke,
    PointerButton,
    PointerTarget,
    ScreenPoint,
    ScrollDelta,
)

NAMES = CuaToolNames()
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nbody").decode("ascii")

WINDOW: dict[str, JsonValue] = {
    "window_id": "win-mail",
    "title": "Mail",
    "application": "Mail",
    "is_active": True,
    "bounds": {"x": 0, "y": 0, "width": 1000, "height": 800},
}
ELEMENT: dict[str, JsonValue] = {
    "element_id": 14,
    "role": "AXTextField",
    "label": "Search",
    "bounds": {"x": 100, "y": 50, "width": 200, "height": 30},
}


@dataclass(slots=True)
class FakeTransport:
    """Records calls and replays scripted replies."""

    replies: dict[str, JsonValue] = field(default_factory=dict)
    tool_names: tuple[str, ...] = field(default_factory=lambda: NAMES.all_names())
    raises: Exception | None = None
    start_raises: Exception | None = None
    calls: list[tuple[str, Mapping[str, JsonValue]]] = field(default_factory=list)
    started: int = 0
    closed: int = 0

    def start(self) -> None:
        self.started += 1
        if self.start_raises is not None:
            raise self.start_raises

    def list_tool_names(self) -> tuple[str, ...]:
        if self.raises is not None:
            raise self.raises
        return self.tool_names

    def call_tool(self, name: str, arguments: Mapping[str, JsonValue]) -> JsonValue:
        self.calls.append((name, dict(arguments)))
        if self.raises is not None:
            raise self.raises
        return self.replies.get(name)

    def close(self) -> None:
        self.closed += 1

    def only_call(self, name: str) -> Mapping[str, JsonValue]:
        matching = [args for called, args in self.calls if called == name]
        assert len(matching) == 1, f"expected one {name!r} call, got {len(matching)}"
        return matching[0]


def _driver(transport: FakeTransport, **kwargs: Any) -> CuaDriverComputerDriver:
    return CuaDriverComputerDriver(transport, CuaDriverSettings(**kwargs))


# --- startup and health ---------------------------------------------------


def test_health_starts_the_driver_and_verifies_every_required_tool() -> None:
    transport = FakeTransport()

    health = _driver(transport).health()

    assert health.available is True
    assert transport.started == 1


@pytest.mark.parametrize("missing", NAMES.all_names())
def test_health_is_unavailable_when_any_single_tool_is_absent(missing: str) -> None:
    """Total, not partial: a driver that can capture but not click is not a
    driver Friday can use, and finding that out at the first click is too late."""
    transport = FakeTransport(
        tool_names=tuple(name for name in NAMES.all_names() if name != missing)
    )

    health = _driver(transport).health()

    assert health.available is False
    assert missing in health.detail


def test_health_reports_unavailable_when_the_driver_cannot_start() -> None:
    transport = FakeTransport(start_raises=ComputerDriverUnavailable("could not be started"))

    health = _driver(transport).health()

    assert health.available is False


def test_health_reports_unavailable_when_the_tool_list_fails() -> None:
    transport = FakeTransport(raises=ComputerDriverTimeout("no answer"))

    health = _driver(transport).health()

    assert health.available is False


def test_close_shuts_the_transport_down() -> None:
    transport = FakeTransport()

    _driver(transport).close()

    assert transport.closed == 1


# --- read-only translation ------------------------------------------------


def test_capture_translation() -> None:
    transport = FakeTransport(
        replies={
            NAMES.capture: {
                "window": WINDOW,
                "elements": [ELEMENT],
                "screenshot": {
                    "data": PNG,
                    "media_type": "image/png",
                    "width": 1000,
                    "height": 800,
                },
            }
        }
    )

    result = _driver(transport).capture(CaptureRequest(window_id="win-mail", max_elements=10))

    assert result.window.window_id == "win-mail"
    assert result.window.bounds.width == 1000
    assert len(result.elements) == 1
    assert result.elements[0].element_id == 14
    # platform role folded to Friday's normalized form
    assert result.elements[0].role == "axtextfield"
    assert result.screenshot is not None
    assert result.screenshot.data.startswith(b"\x89PNG")
    assert transport.only_call(NAMES.capture) == {
        "window_id": "win-mail",
        "include_screenshot": True,
        "include_elements": True,
        "max_elements": 10,
    }


def test_capture_without_a_screenshot_translates_to_none() -> None:
    transport = FakeTransport(replies={NAMES.capture: {"window": WINDOW, "elements": []}})

    result = _driver(transport).capture(CaptureRequest(include_screenshot=False))

    assert result.screenshot is None
    assert result.elements == ()


def test_pointer_position_translation() -> None:
    transport = FakeTransport(replies={NAMES.pointer_position: {"x": 12, "y": -34}})

    assert _driver(transport).pointer_position() == ScreenPoint(x=12, y=-34)


def test_window_list_translation() -> None:
    transport = FakeTransport(replies={NAMES.list_windows: {"windows": [WINDOW, WINDOW]}})

    windows = _driver(transport).list_windows()

    assert len(windows) == 2
    assert windows[0].application == "Mail"


def test_active_window_translation() -> None:
    transport = FakeTransport(replies={NAMES.active_window: {"window": WINDOW}})

    window = _driver(transport).active_window()

    assert window is not None
    assert window.window_id == "win-mail"


def test_active_window_translates_a_null_window() -> None:
    transport = FakeTransport(replies={NAMES.active_window: {"window": None}})

    assert _driver(transport).active_window() is None


# --- mutating translation -------------------------------------------------


def test_pointer_move_translation() -> None:
    transport = FakeTransport(replies={NAMES.move_pointer: {"window_id": "win-mail"}})

    result = _driver(transport).move_pointer(
        PointerTarget(point=ScreenPoint(x=200, y=65), window_id="win-mail")
    )

    assert result.window_id == "win-mail"
    assert transport.only_call(NAMES.move_pointer) == {
        "x": 200,
        "y": 65,
        "window_id": "win-mail",
    }


def test_click_translation() -> None:
    transport = FakeTransport(replies={NAMES.click: {}})

    _driver(transport).click(
        PointerTarget(point=ScreenPoint(x=5, y=6), window_id="win-mail"),
        button=PointerButton.RIGHT,
        count=2,
    )

    assert transport.only_call(NAMES.click) == {
        "x": 5,
        "y": 6,
        "window_id": "win-mail",
        "button": "right",
        "count": 2,
    }


def test_scroll_translation() -> None:
    transport = FakeTransport(replies={NAMES.scroll: {}})

    _driver(transport).scroll(
        PointerTarget(point=ScreenPoint(x=1, y=2), window_id="win-mail"),
        delta=ScrollDelta(dx=0, dy=-300),
    )

    assert transport.only_call(NAMES.scroll) == {
        "x": 1,
        "y": 2,
        "window_id": "win-mail",
        "dx": 0,
        "dy": -300,
    }


def test_type_text_translation() -> None:
    transport = FakeTransport(replies={NAMES.type_text: {}})

    _driver(transport).type_text("hello", window_id="win-mail")

    assert transport.only_call(NAMES.type_text) == {"text": "hello", "window_id": "win-mail"}


def test_keystroke_translation_sends_canonical_modifiers() -> None:
    transport = FakeTransport(replies={NAMES.press_keystroke: {}})

    _driver(transport).press_keystroke(
        Keystroke(key="s", modifiers=(KeyModifier.SHIFT, KeyModifier.META)),
        window_id="win-mail",
    )

    assert transport.only_call(NAMES.press_keystroke) == {
        "key": "s",
        "modifiers": ["meta", "shift"],
        "window_id": "win-mail",
    }


def test_focus_window_translation() -> None:
    transport = FakeTransport(replies={NAMES.focus_window: {}})

    _driver(transport).focus_window("win-mail")

    assert transport.only_call(NAMES.focus_window) == {"window_id": "win-mail"}


def test_a_null_mutation_reply_is_an_empty_result() -> None:
    """Not every driver echoes state back; silence is not a failure."""
    transport = FakeTransport(replies={NAMES.focus_window: None})

    result = _driver(transport).focus_window("win-mail")

    assert result.window_id is None
    assert result.pointer_position is None


def test_a_mutation_reply_may_report_the_observed_pointer() -> None:
    transport = FakeTransport(
        replies={NAMES.click: {"pointer_position": {"x": 7, "y": 8}, "window_id": "win-mail"}}
    )

    result = _driver(transport).click(
        PointerTarget(point=ScreenPoint(x=7, y=8)), button=PointerButton.LEFT, count=1
    )

    assert result.pointer_position == ScreenPoint(x=7, y=8)


# --- malformed replies ----------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        None,
        [],
        "text",
        42,
        {},  # no window
        {"window": None},
        {"window": "win-mail"},
        {"window": {"window_id": "w"}},  # no bounds
        {"window": {**WINDOW, "bounds": {"x": 0, "y": 0, "width": 0, "height": 8}}},
        {"window": {**WINDOW, "window_id": ""}},
        {"window": {**WINDOW, "window_id": 5}},
        {"window": {**WINDOW, "is_active": "yes"}},
        {"window": {**WINDOW, "bounds": {"x": 0, "y": 0, "width": True, "height": 8}}},
        {"window": WINDOW, "elements": "many"},
        {
            "window": WINDOW,
            "elements": [{"element_id": 0, "role": "b", "bounds": WINDOW["bounds"]}],
        },
        {"window": WINDOW, "elements": [{"element_id": 1, "bounds": WINDOW["bounds"]}]},
        {
            "window": WINDOW,
            "elements": [{"element_id": 1, "role": "b", "label": 7, "bounds": WINDOW["bounds"]}],
        },
    ],
)
def test_a_malformed_capture_reply_is_refused(reply: JsonValue) -> None:
    transport = FakeTransport(replies={NAMES.capture: reply})

    with pytest.raises((ComputerDriverFailed, ValueError)):
        _driver(transport).capture(CaptureRequest())


@pytest.mark.parametrize(
    "reply", [None, {}, {"x": 1}, {"x": 1, "y": "2"}, {"x": True, "y": 2}, "point", []]
)
def test_a_malformed_pointer_reply_is_refused(reply: JsonValue) -> None:
    transport = FakeTransport(replies={NAMES.pointer_position: reply})

    with pytest.raises((ComputerDriverFailed, ValueError)):
        _driver(transport).pointer_position()


def test_a_malformed_window_list_reply_is_refused() -> None:
    transport = FakeTransport(replies={NAMES.list_windows: {"windows": "none"}})

    with pytest.raises(ComputerDriverFailed):
        _driver(transport).list_windows()


def test_a_malformed_driver_window_id_is_refused() -> None:
    """§16: an observed window id from a driver is bounded like any other
    observed identifier, not trusted because the driver produced it."""
    transport = FakeTransport(replies={NAMES.click: {"window_id": 42}})

    with pytest.raises(ComputerDriverFailed):
        _driver(transport).click(
            PointerTarget(point=ScreenPoint(x=1, y=1)), button=PointerButton.LEFT, count=1
        )


def test_an_overlong_driver_window_id_is_refused() -> None:
    transport = FakeTransport(replies={NAMES.click: {"window_id": "w" * 500}})

    with pytest.raises(ValueError):
        _driver(transport).click(
            PointerTarget(point=ScreenPoint(x=1, y=1)), button=PointerButton.LEFT, count=1
        )


# --- image bounding -------------------------------------------------------


def test_an_oversized_image_is_refused_before_decoding() -> None:
    """The encoded length is checked first: decoding would allocate the very
    payload the ceiling exists to refuse."""
    huge = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 4096).decode("ascii")
    transport = FakeTransport(
        replies={
            NAMES.capture: {
                "window": WINDOW,
                "screenshot": {"data": huge, "media_type": "image/png", "width": 1, "height": 1},
            }
        }
    )

    with pytest.raises(ComputerDriverFailed, match="capture ceiling"):
        _driver(transport, max_capture_bytes=64).capture(CaptureRequest())


def test_an_undecodable_image_is_refused() -> None:
    transport = FakeTransport(
        replies={
            NAMES.capture: {
                "window": WINDOW,
                "screenshot": {
                    "data": "not!valid!base64!",
                    "media_type": "image/png",
                    "width": 1,
                    "height": 1,
                },
            }
        }
    )

    with pytest.raises(ComputerDriverFailed):
        _driver(transport).capture(CaptureRequest())


@pytest.mark.parametrize(
    "media_type", ["image/svg+xml", "text/html", "application/octet-stream", "image/jpeg", ""]
)
def test_a_disallowed_image_media_type_is_refused(media_type: str) -> None:
    """PNG only. An SVG is scriptable, and an artifact store is not the place to
    discover that a driver chose a different format."""
    transport = FakeTransport(
        replies={
            NAMES.capture: {
                "window": WINDOW,
                "screenshot": {
                    "data": PNG,
                    "media_type": media_type,
                    "width": 10,
                    "height": 10,
                },
            }
        }
    )

    with pytest.raises(ValueError):
        _driver(transport).capture(CaptureRequest())


@pytest.mark.parametrize(
    "screenshot",
    [
        {"media_type": "image/png", "width": 1, "height": 1},  # no data
        {"data": 5, "media_type": "image/png", "width": 1, "height": 1},
        {"data": PNG, "media_type": 7, "width": 1, "height": 1},
        {"data": PNG, "media_type": "image/png", "width": 0, "height": 1},
        {"data": PNG, "media_type": "image/png", "height": 1},
        {"data": PNG, "media_type": "image/png", "width": True, "height": 1},
        {"data": PNG, "media_type": "image/png", "width": 99_999, "height": 1},
    ],
)
def test_a_malformed_screenshot_payload_is_refused(screenshot: JsonValue) -> None:
    transport = FakeTransport(replies={NAMES.capture: {"window": WINDOW, "screenshot": screenshot}})

    with pytest.raises((ComputerDriverFailed, ValueError)):
        _driver(transport).capture(CaptureRequest())


# --- failure propagation --------------------------------------------------


def test_a_transport_timeout_reaches_the_gateway_as_a_timeout() -> None:
    transport = FakeTransport(raises=ComputerDriverTimeout("no answer in 15s"))

    with pytest.raises(ComputerDriverTimeout):
        _driver(transport).pointer_position()


def test_a_dead_process_reaches_the_gateway_as_unavailable() -> None:
    transport = FakeTransport(raises=ComputerDriverUnavailable("exited unexpectedly"))

    with pytest.raises(ComputerDriverUnavailable):
        _driver(transport).pointer_position()


def test_raw_mcp_failure_content_does_not_escape_through_the_gateway(tmp_path: Any) -> None:
    """End to end through the real gateway: a transport error carrying an
    absolute path and a username must arrive as a stable code and nothing else."""
    from friday.infrastructure.tools.computer_gateway import (
        ComputerToolGateway,
        ComputerToolGatewaySettings,
    )
    from tests.infrastructure.computer_harness import MovableClock

    leak = "MCP error -32603 at /Users/patrick/.cua/session.sock for user patrick"
    transport = FakeTransport(raises=ComputerDriverFailed(leak))
    gateway = ComputerToolGateway(
        ComputerToolGatewaySettings(
            driver=_driver(transport), workspace_root=tmp_path, clock=MovableClock()
        )
    )

    from friday.application.tool_gateway import ToolCall, ToolExecutionRequest
    from friday.domain.identifiers import RunId, ToolInvocationId

    result = gateway.execute(
        ToolExecutionRequest(
            invocation_id=ToolInvocationId.new(),
            run_id=RunId.new(),
            step_id=None,
            call=ToolCall(tool="computer.window_list", tool_input={}),
        )
    )

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.retryable is False
    for fragment in ("patrick", "/Users", "MCP", "-32603", "session.sock"):
        assert fragment not in result.failure.message


# --- no generic escape hatch ---------------------------------------------


def test_the_adapter_exposes_no_generic_call_surface() -> None:
    """Claude must not be able to reach an MCP tool Friday never declared, so
    there is no `call(action, payload)` on the driver at all."""
    driver = _driver(FakeTransport())

    assert not hasattr(driver, "call")
    assert not hasattr(driver, "invoke")
    assert not hasattr(driver, "call_tool")


def test_the_tool_name_table_covers_exactly_the_driver_methods() -> None:
    """One slot per ComputerDriver method — no more, so an override cannot widen
    what Friday is able to invoke."""
    names = NAMES.all_names()

    assert len(names) == 10
    assert len(set(names)) == 10
