"""CuaDriverComputerDriver against a fake MCP transport.

No real desktop and no real subprocess: the adapter's job is translation and
distrust, and both are fully observable at the transport seam. CI must never
depend on a machine having a window server.

Two properties carry most of the weight:

* **Every reply is validated** on the read paths (`list_windows`, `find_window`,
  `capture`, `cursor_position`). A driver is untrusted infrastructure input, so
  a missing field, a wrong type, or a bool where an int belongs must raise
  rather than be coerced into something plausible.
* **Mutating replies are read leniently.** cua wraps every result in a text
  summary and only some tools add structured content, so a click that landed
  must not be reported as a driver failure merely because its reply carried no
  usable `effect`/`verified` fields.
* **Nothing from a failure escapes.** Transport errors, JSON-RPC error bodies,
  and stderr text can quote absolute paths, usernames, and window contents, so
  the messages that cross the boundary are constants.

The startup health check is tested as a *total* check: a driver missing one of
the nine tools is unavailable now, not working-until-the-first-click.
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
from friday.infrastructure.computer.mcp_stdio import McpImageContent, McpToolResult
from friday.infrastructure.computer.models import (
    CaptureRequest,
    ElementDescriptor,
    ElementTarget,
    KeyModifier,
    Keystroke,
    PixelPoint,
    PixelTarget,
    PointerButton,
    ScrollCommand,
    ScrollDirection,
    ScrollGranularity,
    WindowRef,
    WindowTarget,
)

NAMES = CuaToolNames()
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nbody").decode("ascii")

MAIL_PID = 844
MAIL_WINDOW_ID = 10725


def mail_ref() -> WindowRef:
    return WindowRef(pid=MAIL_PID, window_id=MAIL_WINDOW_ID)


WINDOW: dict[str, JsonValue] = {
    "pid": MAIL_PID,
    "window_id": MAIL_WINDOW_ID,
    "title": "Mail",
    "app_name": "Mail",
    "z_index": 5,
    "is_on_screen": True,
    "on_current_space": True,
    "bounds": {"x": 0, "y": 0, "width": 1000, "height": 800},
}
ELEMENT: dict[str, JsonValue] = {
    "element_index": 14,
    "role": "AXTextField",
    "label": "Search",
    "element_token": "tok-search",
    "frame": {"x": 100, "y": 50, "w": 200, "h": 30},
}


@dataclass(slots=True)
class FakeTransport:
    """Records calls and replays scripted replies."""

    replies: dict[str, JsonValue | McpToolResult] = field(default_factory=dict)
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

    def call_tool(
        self, name: str, arguments: Mapping[str, JsonValue], *, require_payload: bool = True
    ) -> McpToolResult:
        del require_payload
        self.calls.append((name, dict(arguments)))
        if self.raises is not None:
            raise self.raises
        reply = self.replies.get(name)
        if isinstance(reply, McpToolResult):
            return reply
        return McpToolResult(structured_content=reply)

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
            NAMES.window_state: {
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

    result = _driver(transport).capture(CaptureRequest(ref=mail_ref(), max_elements=10))

    assert len(result.elements) == 1
    assert result.elements[0].element_index == 14
    # platform role folded to Friday's normalized form
    assert result.elements[0].role == "axtextfield"
    assert result.elements[0].frame.width == 200
    assert result.screenshot is not None
    assert result.screenshot.data.startswith(b"\x89PNG")
    assert transport.only_call(NAMES.window_state) == {
        "pid": MAIL_PID,
        "window_id": MAIL_WINDOW_ID,
        "include_screenshot": True,
        "max_elements": 10,
    }


def test_capture_without_a_screenshot_translates_to_none() -> None:
    transport = FakeTransport(replies={NAMES.window_state: {"elements": []}})

    result = _driver(transport).capture(CaptureRequest(ref=mail_ref(), include_screenshot=False))

    assert result.screenshot is None
    assert result.elements == ()


def test_capture_joins_structured_elements_and_mcp_image_block() -> None:
    """Cua's real get_window_state response carries these as sibling blocks."""
    transport = FakeTransport(
        replies={
            NAMES.window_state: McpToolResult(
                structured_content={
                    "elements": [ELEMENT],
                    "screenshot": {"width": 1000, "height": 800},
                },
                text_content={"tree_markdown": "ignored for structured consumers"},
                image_content=(McpImageContent(data=PNG, media_type="image/png"),),
            )
        }
    )

    result = _driver(transport).capture(CaptureRequest(ref=mail_ref()))

    assert result.elements[0].element_index == 14
    assert result.screenshot is not None
    assert result.screenshot.data.startswith(b"\x89PNG")
    assert result.screenshot.extent.width == 1000
    assert result.screenshot.extent.height == 800


def test_capture_caps_max_elements_at_the_configured_ceiling() -> None:
    transport = FakeTransport(replies={NAMES.window_state: {"elements": []}})

    _driver(transport, max_elements=5).capture(CaptureRequest(ref=mail_ref(), max_elements=500))

    assert transport.only_call(NAMES.window_state)["max_elements"] == 5


def test_cursor_position_translation() -> None:
    transport = FakeTransport(replies={NAMES.cursor_position: {"x": 12, "y": -34}})

    point = _driver(transport).cursor_position()

    assert (point.x, point.y) == (12, -34)


def test_window_list_translation() -> None:
    transport = FakeTransport(replies={NAMES.list_windows: {"windows": [WINDOW, WINDOW]}})

    windows = _driver(transport).list_windows()

    assert len(windows) == 2
    assert windows[0].ref == mail_ref()
    assert windows[0].app_name == "Mail"
    assert transport.only_call(NAMES.list_windows) == {}


def test_find_window_filters_by_pid_then_matches_the_window_id() -> None:
    transport = FakeTransport(replies={NAMES.list_windows: {"windows": [WINDOW]}})

    window = _driver(transport).find_window(mail_ref())

    assert window is not None
    assert window.ref == mail_ref()
    assert transport.only_call(NAMES.list_windows) == {"pid": MAIL_PID}


def test_find_window_returns_none_when_the_window_is_absent() -> None:
    transport = FakeTransport(replies={NAMES.list_windows: {"windows": []}})

    assert _driver(transport).find_window(mail_ref()) is None


def test_find_window_returns_none_when_the_pid_has_a_different_window() -> None:
    other = {**WINDOW, "window_id": 99}
    transport = FakeTransport(replies={NAMES.list_windows: {"windows": [other]}})

    assert _driver(transport).find_window(mail_ref()) is None


# --- mutating translation: addressing modes --------------------------------


def test_click_with_a_pixel_target() -> None:
    transport = FakeTransport(replies={NAMES.click: {}})

    _driver(transport).click(
        PixelTarget(ref=mail_ref(), point=PixelPoint(x=5, y=6)),
        button=PointerButton.RIGHT,
        count=2,
    )

    assert transport.only_call(NAMES.click) == {
        "pid": MAIL_PID,
        "window_id": MAIL_WINDOW_ID,
        "x": 5,
        "y": 6,
        "button": "right",
        "count": 2,
    }


def test_click_with_an_element_target_carries_the_token() -> None:
    transport = FakeTransport(replies={NAMES.click: {}})
    target = ElementTarget(
        ref=mail_ref(),
        element_index=15,
        descriptor=ElementDescriptor(role="button", label="Send"),
        element_token="tok-send",
    )

    _driver(transport).click(target, button=PointerButton.LEFT, count=1)

    assert transport.only_call(NAMES.click) == {
        "pid": MAIL_PID,
        "window_id": MAIL_WINDOW_ID,
        "element_index": 15,
        "element_token": "tok-send",
        "button": "left",
        "count": 1,
    }


def test_click_with_an_element_target_without_a_token_omits_it() -> None:
    transport = FakeTransport(replies={NAMES.click: {}})
    target = ElementTarget(
        ref=mail_ref(),
        element_index=15,
        descriptor=ElementDescriptor(role="button", label="Send"),
    )

    _driver(transport).click(target, button=PointerButton.LEFT, count=1)

    assert transport.only_call(NAMES.click) == {
        "pid": MAIL_PID,
        "window_id": MAIL_WINDOW_ID,
        "element_index": 15,
        "button": "left",
        "count": 1,
    }


def test_scroll_with_a_window_target_has_no_x_y_or_element_fields() -> None:
    transport = FakeTransport(replies={NAMES.scroll: {}})

    _driver(transport).scroll(
        WindowTarget(ref=mail_ref()),
        command=ScrollCommand(direction=ScrollDirection.DOWN, amount=5, by=ScrollGranularity.PAGE),
    )

    assert transport.only_call(NAMES.scroll) == {
        "pid": MAIL_PID,
        "window_id": MAIL_WINDOW_ID,
        "direction": "down",
        "amount": 5,
        "by": "page",
    }


def test_scroll_with_a_pixel_target() -> None:
    transport = FakeTransport(replies={NAMES.scroll: {}})

    _driver(transport).scroll(
        PixelTarget(ref=mail_ref(), point=PixelPoint(x=1, y=2)),
        command=ScrollCommand(direction=ScrollDirection.UP),
    )

    assert transport.only_call(NAMES.scroll) == {
        "pid": MAIL_PID,
        "window_id": MAIL_WINDOW_ID,
        "x": 1,
        "y": 2,
        "direction": "up",
        "amount": 3,
        "by": "line",
    }


def test_type_text_translation() -> None:
    transport = FakeTransport(replies={NAMES.type_text: {}})
    target = ElementTarget(
        ref=mail_ref(), element_index=14, descriptor=ElementDescriptor(role="text_field", label="")
    )

    _driver(transport).type_text("hello", target=target)

    assert transport.only_call(NAMES.type_text) == {
        "pid": MAIL_PID,
        "window_id": MAIL_WINDOW_ID,
        "element_index": 14,
        "text": "hello",
    }


# --- mutating translation: keys --------------------------------------------


def test_press_key_translation_maps_a_named_key() -> None:
    transport = FakeTransport(replies={NAMES.press_key: {}})

    _driver(transport).press_key(Keystroke(key="enter"), target=WindowTarget(ref=mail_ref()))

    assert transport.only_call(NAMES.press_key) == {
        "pid": MAIL_PID,
        "window_id": MAIL_WINDOW_ID,
        "key": "return",
    }


def test_press_key_passes_a_single_character_through_unchanged() -> None:
    transport = FakeTransport(replies={NAMES.press_key: {}})

    _driver(transport).press_key(Keystroke(key="a"), target=WindowTarget(ref=mail_ref()))

    assert transport.only_call(NAMES.press_key)["key"] == "a"


def test_hotkey_sends_modifiers_before_the_key_in_canonical_order() -> None:
    transport = FakeTransport(replies={NAMES.hotkey: {}})

    _driver(transport).hotkey(
        Keystroke(key="s", modifiers=(KeyModifier.SHIFT, KeyModifier.META)),
        target=WindowTarget(ref=mail_ref()),
    )

    assert transport.only_call(NAMES.hotkey) == {
        "pid": MAIL_PID,
        "window_id": MAIL_WINDOW_ID,
        "keys": ["cmd", "shift", "s"],
    }


def test_hotkey_maps_alt_to_option() -> None:
    transport = FakeTransport(replies={NAMES.hotkey: {}})

    _driver(transport).hotkey(
        Keystroke(key="tab", modifiers=(KeyModifier.ALT,)), target=WindowTarget(ref=mail_ref())
    )

    assert transport.only_call(NAMES.hotkey)["keys"] == ["option", "tab"]


def test_bring_to_front_translation() -> None:
    transport = FakeTransport(replies={NAMES.bring_to_front: {}})

    _driver(transport).bring_to_front(mail_ref())

    assert transport.only_call(NAMES.bring_to_front) == {
        "pid": MAIL_PID,
        "window_id": MAIL_WINDOW_ID,
    }


# --- mutating replies are read leniently -----------------------------------


def test_a_non_dict_mutation_reply_is_an_empty_result() -> None:
    """Not every driver echoes structured detail back; silence is not a
    failure — the side effect already happened."""
    transport = FakeTransport(replies={NAMES.bring_to_front: None})

    result = _driver(transport).bring_to_front(mail_ref())

    assert result.effect is None
    assert result.verified is None


def test_a_mutation_reply_reports_effect_and_verified() -> None:
    transport = FakeTransport(replies={NAMES.click: {"effect": "clicked", "verified": True}})

    result = _driver(transport).click(
        PixelTarget(ref=mail_ref(), point=PixelPoint(x=1, y=1)),
        button=PointerButton.LEFT,
        count=1,
    )

    assert result.effect == "clicked"
    assert result.verified is True


def test_mistyped_effect_and_verified_fields_are_ignored_not_raised() -> None:
    """Action replies are read leniently: a driver reply Friday cannot make
    sense of is treated as "no structured detail", never as a failure — only
    reads are held to strict typing."""
    transport = FakeTransport(replies={NAMES.click: {"effect": 123, "verified": "yes"}})

    result = _driver(transport).click(
        PixelTarget(ref=mail_ref(), point=PixelPoint(x=1, y=1)),
        button=PointerButton.LEFT,
        count=1,
    )

    assert result.effect is None
    assert result.verified is None


# --- malformed replies: list_windows ---------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        None,
        [],
        "text",
        42,
        {},  # no "windows"
        {"windows": "not-a-list"},
        {"windows": [{}]},  # entry missing pid
        {"windows": [{**WINDOW, "pid": "x"}]},
        {"windows": [{**WINDOW, "window_id": True}]},
        {"windows": [{**WINDOW, "bounds": {"x": 0, "y": 0, "width": 0, "height": 8}}]},
        {"windows": [{**WINDOW, "is_on_screen": "yes"}]},
        {"windows": [{k: v for k, v in WINDOW.items() if k != "bounds"}]},
    ],
)
def test_a_malformed_window_list_reply_is_refused(reply: JsonValue) -> None:
    transport = FakeTransport(replies={NAMES.list_windows: reply})

    with pytest.raises((ComputerDriverFailed, ValueError)):
        _driver(transport).list_windows()


# --- malformed replies: capture / window_state -----------------------------


@pytest.mark.parametrize(
    "reply",
    [
        None,
        [],
        "text",
        42,
        {"elements": "many"},
        {"elements": [{"role": "b", "frame": ELEMENT["frame"]}]},  # no element_index
        {"elements": [{"element_index": 1, "frame": ELEMENT["frame"]}]},  # no role
        {"elements": [{"element_index": 1, "role": "", "frame": ELEMENT["frame"]}]},
        {"elements": [{"element_index": -1, "role": "b", "frame": ELEMENT["frame"]}]},
        {"elements": [{"element_index": 1, "role": "b"}]},  # no frame
        {
            "elements": [
                {"element_index": 1, "role": "b", "frame": {"x": 0, "y": 0, "w": 0, "h": 8}}
            ]
        },
        {"elements": [{"element_index": 1, "role": "b", "frame": ELEMENT["frame"], "label": 7}]},
        {
            "elements": [
                {
                    "element_index": 1,
                    "role": "b",
                    "frame": ELEMENT["frame"],
                    "element_token": "",
                }
            ]
        },
    ],
)
def test_a_malformed_capture_reply_is_refused(reply: JsonValue) -> None:
    transport = FakeTransport(replies={NAMES.window_state: reply})

    with pytest.raises((ComputerDriverFailed, ValueError)):
        _driver(transport).capture(CaptureRequest(ref=mail_ref()))


# --- malformed replies: cursor_position ------------------------------------


@pytest.mark.parametrize(
    "reply", [None, {}, {"x": 1}, {"x": 1, "y": "2"}, {"x": True, "y": 2}, "point", []]
)
def test_a_malformed_cursor_position_reply_is_refused(reply: JsonValue) -> None:
    transport = FakeTransport(replies={NAMES.cursor_position: reply})

    with pytest.raises((ComputerDriverFailed, ValueError)):
        _driver(transport).cursor_position()


# --- image bounding ---------------------------------------------------------


def test_an_oversized_image_is_refused_before_decoding() -> None:
    """The encoded length is checked first: decoding would allocate the very
    payload the ceiling exists to refuse."""
    huge = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 4096).decode("ascii")
    transport = FakeTransport(
        replies={
            NAMES.window_state: {
                "elements": [],
                "screenshot": {"data": huge, "media_type": "image/png", "width": 1, "height": 1},
            }
        }
    )

    with pytest.raises(ComputerDriverFailed, match="capture ceiling"):
        _driver(transport, max_capture_bytes=64).capture(CaptureRequest(ref=mail_ref()))


def test_an_undecodable_image_is_refused() -> None:
    transport = FakeTransport(
        replies={
            NAMES.window_state: {
                "elements": [],
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
        _driver(transport).capture(CaptureRequest(ref=mail_ref()))


@pytest.mark.parametrize(
    "media_type", ["image/svg+xml", "text/html", "application/octet-stream", "image/jpeg", ""]
)
def test_a_disallowed_image_media_type_is_refused(media_type: str) -> None:
    """PNG only. An SVG is scriptable, and an artifact store is not the place to
    discover that a driver chose a different format."""
    transport = FakeTransport(
        replies={
            NAMES.window_state: {
                "elements": [],
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
        _driver(transport).capture(CaptureRequest(ref=mail_ref()))


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
    transport = FakeTransport(
        replies={NAMES.window_state: {"elements": [], "screenshot": screenshot}}
    )

    with pytest.raises((ComputerDriverFailed, ValueError)):
        _driver(transport).capture(CaptureRequest(ref=mail_ref()))


# --- failure propagation ----------------------------------------------------


def test_a_transport_timeout_reaches_the_gateway_as_a_timeout() -> None:
    transport = FakeTransport(raises=ComputerDriverTimeout("no answer in 15s"))

    with pytest.raises(ComputerDriverTimeout):
        _driver(transport).cursor_position()


def test_a_dead_process_reaches_the_gateway_as_unavailable() -> None:
    transport = FakeTransport(raises=ComputerDriverUnavailable("exited unexpectedly"))

    with pytest.raises(ComputerDriverUnavailable):
        _driver(transport).cursor_position()


def test_raw_mcp_failure_content_does_not_escape_through_the_gateway(tmp_path: Any) -> None:
    """End to end through the real gateway: a transport error carrying an
    absolute path and a username must arrive as a stable code and nothing else."""
    from friday.infrastructure.tools.computer_gateway import (
        ComputerToolGateway,
        ComputerToolGatewaySettings,
    )
    from tests.infrastructure.computer_harness import FixedClock

    leak = "MCP error -32603 at /Users/patrick/.cua/session.sock for user patrick"
    transport = FakeTransport(raises=ComputerDriverFailed(leak))
    gateway = ComputerToolGateway(
        ComputerToolGatewaySettings(
            driver=_driver(transport), workspace_root=tmp_path, clock=FixedClock()
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


# --- no generic escape hatch ------------------------------------------------


def test_the_adapter_exposes_no_generic_call_surface() -> None:
    """Claude must not be able to reach an MCP tool Friday never declared, so
    there is no `call(action, payload)` on the driver at all."""
    driver = _driver(FakeTransport())

    assert not hasattr(driver, "call")
    assert not hasattr(driver, "invoke")
    assert not hasattr(driver, "call_tool")


def test_the_tool_name_table_covers_exactly_the_driver_methods() -> None:
    """One slot per MCP tool Friday calls — no more, so an override cannot widen
    what Friday is able to invoke. `find_window` reuses `list_windows` with a
    pid filter rather than naming a tenth tool."""
    names = NAMES.all_names()

    assert len(names) == 9
    assert len(set(names)) == 9
