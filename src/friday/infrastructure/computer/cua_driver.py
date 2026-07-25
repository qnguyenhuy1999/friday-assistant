"""CuaDriverComputerDriver — the production ComputerDriver, backed by cua-driver
over MCP stdio.

This is a transport, not a policy layer. By the time a method here is called,
the gateway has already resolved a snapshot, proven the target lies inside a
captured window, screened the text, and checked the hotkey deny-list. The
adapter's job is to translate typed values into one MCP tool call and translate
the reply back into typed values — and to refuse anything that does not
translate cleanly.

**cua-driver is not a Claude tool.** Claude never sees this MCP server, cannot
name it, and cannot reach it: the only caller is ComputerToolGateway, and the
only tools invoked are the ten in `CuaToolNames`. There is deliberately no
generic `call(action, payload)` here for a higher layer to discover.

Driver replies are untrusted infrastructure input, exactly like the desktop text
inside them. Every field is read with an explicit type check and handed to a
value object that bounds it; a reply that does not fit raises
ComputerDriverFailed with a constant message rather than being coerced into
something plausible. Image bytes are bounded *before* base64 decoding, so an
oversized capture cannot be materialized in memory just to be rejected after.

Tool names are a fixed, reviewable default table rather than free-form
configuration, and `health()` verifies every one of them against `tools/list`
at startup. If the installed cua-driver build names its tools differently,
Friday fails closed with an operator-facing message instead of discovering the
mismatch at the moment of a click.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Final

from friday.domain.json_value import JsonValue
from friday.infrastructure.computer.driver import ComputerDriverHealth
from friday.infrastructure.computer.errors import (
    ComputerDriverFailed,
    ComputerDriverUnavailable,
    ComputerUseError,
)
from friday.infrastructure.computer.mcp_stdio import McpTransport
from friday.infrastructure.computer.models import (
    CapturedElement,
    CaptureRequest,
    CaptureResult,
    DriverResult,
    Keystroke,
    PointerButton,
    PointerTarget,
    ScreenBounds,
    ScreenPoint,
    Screenshot,
    ScrollDelta,
    WindowInfo,
)

DEFAULT_MAX_CAPTURE_BYTES = 8_000_000
_BASE64_EXPANSION = 4 / 3


@dataclass(frozen=True, slots=True)
class CuaToolNames:
    """The assumed cua-driver MCP surface, one slot per ComputerDriver method.

    Overridable so a differently-named build can be adapted without a code
    change — but only these ten slots exist, so no override can widen what
    Friday is able to invoke.
    """

    capture: str = "capture_window"
    pointer_position: str = "pointer_position"
    list_windows: str = "list_windows"
    active_window: str = "active_window"
    move_pointer: str = "move_pointer"
    click: str = "click"
    scroll: str = "scroll"
    type_text: str = "type_text"
    press_keystroke: str = "press_key"
    focus_window: str = "focus_window"

    def all_names(self) -> tuple[str, ...]:
        return tuple(getattr(self, item.name) for item in fields(self))


@dataclass(frozen=True, slots=True)
class CuaDriverSettings:
    max_capture_bytes: int = DEFAULT_MAX_CAPTURE_BYTES
    tool_names: CuaToolNames = CuaToolNames()

    def __post_init__(self) -> None:
        if self.max_capture_bytes < 1:
            raise ValueError("CuaDriverSettings.max_capture_bytes must be positive")


class CuaDriverComputerDriver:
    """ComputerDriver over an MCP stdio transport."""

    def __init__(self, transport: McpTransport, settings: CuaDriverSettings | None = None) -> None:
        self._transport = transport
        self._settings = settings or CuaDriverSettings()

    # --- lifecycle --------------------------------------------------------

    def health(self) -> ComputerDriverHealth:
        """Start the driver and verify it exposes every tool Friday will call.

        Deterministic and total: a driver that starts but is missing one tool is
        reported unavailable now, rather than working for capture and failing at
        the first click. `detail` is operator-facing preflight text and never
        reaches the brain.
        """
        expected = self._settings.tool_names.all_names()
        try:
            self._transport.start()
            available = set(self._transport.list_tool_names())
        except ComputerUseError as exc:
            return ComputerDriverHealth(available=False, detail=str(exc))
        missing = sorted(name for name in expected if name not in available)
        if missing:
            return ComputerDriverHealth(
                available=False,
                detail=f"cua-driver does not expose required tool(s): {missing}",
            )
        return ComputerDriverHealth(
            available=True, detail=f"cua-driver ready with {len(expected)} required tools"
        )

    def close(self) -> None:
        self._transport.close()

    # --- read-only observation -------------------------------------------

    def capture(self, request: CaptureRequest) -> CaptureResult:
        payload = self._call(
            self._settings.tool_names.capture,
            {
                "window_id": request.window_id,
                "include_screenshot": request.include_screenshot,
                "include_elements": request.include_elements,
                "max_elements": request.max_elements,
            },
        )
        body = _object(payload)
        window = _window(_object(_required(body, "window")))
        elements = tuple(
            _element(_object(entry)) for entry in _array(body.get("elements", []), "elements")
        )
        screenshot = self._screenshot(body.get("screenshot"))
        return CaptureResult(window=window, elements=elements, screenshot=screenshot)

    def pointer_position(self) -> ScreenPoint:
        payload = self._call(self._settings.tool_names.pointer_position, {})
        return _point(_object(payload))

    def list_windows(self) -> tuple[WindowInfo, ...]:
        payload = self._call(self._settings.tool_names.list_windows, {})
        body = _object(payload)
        return tuple(
            _window(_object(entry)) for entry in _array(_required(body, "windows"), "windows")
        )

    def active_window(self) -> WindowInfo | None:
        payload = self._call(self._settings.tool_names.active_window, {})
        window = _object(payload).get("window")
        if window is None:
            return None
        return _window(_object(window))

    # --- mutating input ---------------------------------------------------

    def move_pointer(self, target: PointerTarget) -> DriverResult:
        return self._mutate(self._settings.tool_names.move_pointer, _target_arguments(target))

    def click(self, target: PointerTarget, *, button: PointerButton, count: int) -> DriverResult:
        return self._mutate(
            self._settings.tool_names.click,
            {**_target_arguments(target), "button": button.value, "count": count},
        )

    def scroll(self, target: PointerTarget, *, delta: ScrollDelta) -> DriverResult:
        return self._mutate(
            self._settings.tool_names.scroll,
            {**_target_arguments(target), "dx": delta.dx, "dy": delta.dy},
        )

    def type_text(self, text: str, *, window_id: str | None) -> DriverResult:
        return self._mutate(
            self._settings.tool_names.type_text, {"text": text, "window_id": window_id}
        )

    def press_keystroke(self, keystroke: Keystroke, *, window_id: str | None) -> DriverResult:
        return self._mutate(
            self._settings.tool_names.press_keystroke,
            {
                "key": keystroke.key,
                "modifiers": [modifier.value for modifier in keystroke.modifiers],
                "window_id": window_id,
            },
        )

    def focus_window(self, window_id: str) -> DriverResult:
        return self._mutate(self._settings.tool_names.focus_window, {"window_id": window_id})

    # --- internals --------------------------------------------------------

    def _call(self, name: str, arguments: Mapping[str, JsonValue]) -> JsonValue:
        if not self._transport_ready():
            raise ComputerDriverUnavailable("the computer-use driver is not running")
        return self._transport.call_tool(name, arguments)

    def _transport_ready(self) -> bool:
        """`start()` is idempotent, so calling it here costs nothing on the
        happy path and removes a class of "driver used before preflight" bugs."""
        self._transport.start()
        return True

    def _mutate(self, name: str, arguments: Mapping[str, JsonValue]) -> DriverResult:
        payload = self._call(name, arguments)
        if payload is None:
            return DriverResult()
        body = _object(payload)
        position = body.get("pointer_position")
        window_id = body.get("window_id")
        if window_id is not None and not isinstance(window_id, str):
            raise ComputerDriverFailed("the computer-use driver returned a malformed window id")
        return DriverResult(
            pointer_position=_point(_object(position)) if position is not None else None,
            window_id=window_id,
        )

    def _screenshot(self, value: JsonValue) -> Screenshot | None:
        if value is None:
            return None
        body = _object(value)
        encoded = _required(body, "data")
        if not isinstance(encoded, str):
            raise ComputerDriverFailed("the computer-use driver returned a malformed image payload")
        self._reject_oversized(encoded)
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ComputerDriverFailed(
                "the computer-use driver returned an undecodable image payload"
            ) from exc
        if len(data) > self._settings.max_capture_bytes:
            raise ComputerDriverFailed("the captured image exceeds the configured capture ceiling")
        media_type = body.get("media_type", "image/png")
        if not isinstance(media_type, str):
            raise ComputerDriverFailed("the computer-use driver returned a malformed media type")
        return Screenshot(
            data=data,
            media_type=media_type,
            width=_int(body, "width"),
            height=_int(body, "height"),
        )

    def _reject_oversized(self, encoded: str) -> None:
        """Bound the encoded string before decoding it.

        Decoding first would allocate the very payload the ceiling exists to
        refuse. Base64 inflates by 4/3, so the encoded length is a sound
        pre-decode proxy for the decoded size.
        """
        ceiling = int(self._settings.max_capture_bytes * _BASE64_EXPANSION) + 4
        if len(encoded) > ceiling:
            raise ComputerDriverFailed("the captured image exceeds the configured capture ceiling")


# --- strict readers -------------------------------------------------------
#
# Every one raises ComputerDriverFailed with a constant message. A driver reply
# is untrusted input, and its offending value may be observed window content, so
# it is never quoted back.

_MALFORMED: Final = "the computer-use driver returned a malformed response"


def _object(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ComputerDriverFailed(_MALFORMED)
    return value


def _array(value: JsonValue, field_name: str) -> list[JsonValue]:
    del field_name
    if not isinstance(value, list):
        raise ComputerDriverFailed(_MALFORMED)
    return value


def _required(body: dict[str, JsonValue], name: str) -> JsonValue:
    if name not in body:
        raise ComputerDriverFailed(_MALFORMED)
    return body[name]


def _int(body: dict[str, JsonValue], name: str) -> int:
    value = _required(body, name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ComputerDriverFailed(_MALFORMED)
    return value


def _str(body: dict[str, JsonValue], name: str, *, default: str | None = None) -> str:
    value = body.get(name, default)
    if not isinstance(value, str):
        raise ComputerDriverFailed(_MALFORMED)
    return value


def _bool(body: dict[str, JsonValue], name: str, *, default: bool) -> bool:
    value = body.get(name, default)
    if not isinstance(value, bool):
        raise ComputerDriverFailed(_MALFORMED)
    return value


def _point(body: dict[str, JsonValue]) -> ScreenPoint:
    return ScreenPoint(x=_int(body, "x"), y=_int(body, "y"))


def _bounds(body: dict[str, JsonValue]) -> ScreenBounds:
    return ScreenBounds(
        x=_int(body, "x"),
        y=_int(body, "y"),
        width=_int(body, "width"),
        height=_int(body, "height"),
    )


def _window(body: dict[str, JsonValue]) -> WindowInfo:
    return WindowInfo(
        window_id=_str(body, "window_id"),
        title=_str(body, "title", default=""),
        bounds=_bounds(_object(_required(body, "bounds"))),
        application=_str(body, "application", default=""),
        is_active=_bool(body, "is_active", default=False),
    )


def _element(body: dict[str, JsonValue]) -> CapturedElement:
    label = body.get("label")
    if label is not None and not isinstance(label, str):
        raise ComputerDriverFailed(_MALFORMED)
    return CapturedElement(
        element_id=_int(body, "element_id"),
        role=_str(body, "role"),
        bounds=_bounds(_object(_required(body, "bounds"))),
        label=label,
    )


def _target_arguments(target: PointerTarget) -> dict[str, JsonValue]:
    return {"x": target.point.x, "y": target.point.y, "window_id": target.window_id}
