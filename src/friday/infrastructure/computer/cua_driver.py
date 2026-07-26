"""CuaDriverComputerDriver — the production ComputerDriver, backed by cua-driver
over MCP stdio.

This is a transport, not a policy layer. By the time a method here is called,
the gateway has already captured the window, proven the approved target still
resolves in what it just saw, screened the text, and checked the hotkey
deny-list. The adapter's job is to translate typed values into one MCP tool call
and translate the reply back into typed values — and to refuse anything that
does not translate cleanly.

**cua-driver is not a Claude tool.** Claude never sees this MCP server, cannot
name it, and cannot reach it: the only caller is ComputerToolGateway, and the
only tools invoked are the nine in `CuaToolNames`. There is deliberately no
generic `call(action, payload)` here for a higher layer to discover.

The mapping follows cua-driver's published MCP surface rather than a plausible
guess at it, and the differences are load-bearing:

* `pid` and `window_id` are **integers**, and both are required to address a
  window. `window_id` alone is not addressable.
* Elements are addressed by `element_index` (plus `element_token`, an opaque
  per-snapshot handle the driver can recognize as stale), never by a name Friday
  invented.
* Action coordinates are **window-local screenshot pixels**, read straight off
  the image `get_window_state` returned — not desktop coordinates. The driver
  undoes Retina scaling internally, so the pixel read is the pixel clicked.
* Window metadata comes from `list_windows`; `get_window_state` walks one
  window's contents. Two calls, two questions.
* `press_key` and `hotkey` are separate operations, so a keystroke with
  modifiers routes to a different tool than one without.

Driver replies are untrusted infrastructure input, exactly like the desktop text
inside them. Every field is read with an explicit type check and handed to a
value object that bounds it; a reply that does not fit raises
ComputerDriverFailed with a constant message rather than being coerced into
something plausible. Image bytes are bounded *before* base64 decoding, so an
oversized capture cannot be materialized in memory just to be rejected after.

Action replies are read leniently on purpose. cua wraps every result in a text
summary and only some tools add structured content, so a click that landed must
not be reported as a driver failure merely because its reply carried no JSON
body. Reads stay strict: an observation Friday cannot parse is an observation it
does not have.

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
from friday.infrastructure.computer.mcp_stdio import McpImageContent, McpToolResult, McpTransport
from friday.infrastructure.computer.models import (
    ActionTarget,
    AddressedTarget,
    CapturedElement,
    CaptureRequest,
    CaptureResult,
    DriverResult,
    ElementTarget,
    Keystroke,
    PixelFrame,
    PixelTarget,
    PointerButton,
    ScreenBounds,
    ScreenPoint,
    Screenshot,
    ScrollCommand,
    WindowInfo,
    WindowRef,
    WindowTarget,
)

DEFAULT_MAX_CAPTURE_BYTES = 8_000_000
_BASE64_EXPANSION = 4 / 3

_MODIFIER_NAMES: Final = {
    "meta": "cmd",
    "ctrl": "ctrl",
    "alt": "option",
    "shift": "shift",
}
"""Friday's modifier vocabulary onto cua's. `meta` is `cmd` and `alt` is
`option`: a closed mapping, so an unmapped modifier is a KeyError here rather
than a string the driver silently ignores."""

_KEY_NAMES: Final = {
    "enter": "return",
    "escape": "escape",
    "tab": "tab",
    "space": "space",
    "backspace": "delete",
    "arrow_up": "up",
    "arrow_down": "down",
    "arrow_left": "left",
    "arrow_right": "right",
    "home": "home",
    "end": "end",
    "page_up": "pageup",
    "page_down": "pagedown",
}
"""Friday's named keys onto cua's. Single `[a-z0-9]` characters pass through
unchanged; anything else must appear here or the call is refused rather than
sent as a name the driver may not recognize.

`backspace` maps to cua's documented `delete`. Forward-delete is intentionally
not exposed until the installed driver contract proves a backing key name."""


@dataclass(frozen=True, slots=True)
class CuaToolNames:
    """The cua-driver MCP surface Friday uses, one slot per driver operation.

    Overridable so a differently-named build can be adapted without a code
    change — but only these nine slots exist, so no override can widen what
    Friday is able to invoke.
    """

    list_windows: str = "list_windows"
    window_state: str = "get_window_state"
    cursor_position: str = "get_cursor_position"
    click: str = "click"
    scroll: str = "scroll"
    type_text: str = "type_text"
    press_key: str = "press_key"
    hotkey: str = "hotkey"
    bring_to_front: str = "bring_to_front"

    def all_names(self) -> tuple[str, ...]:
        return tuple(getattr(self, item.name) for item in fields(self))


@dataclass(frozen=True, slots=True)
class CuaDriverSettings:
    max_capture_bytes: int = DEFAULT_MAX_CAPTURE_BYTES
    max_elements: int = 2_000
    tool_names: CuaToolNames = CuaToolNames()

    def __post_init__(self) -> None:
        if self.max_capture_bytes < 1:
            raise ValueError("CuaDriverSettings.max_capture_bytes must be positive")
        if self.max_elements < 1:
            raise ValueError("CuaDriverSettings.max_elements must be positive")


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

    def list_windows(self) -> tuple[WindowInfo, ...]:
        payload = self._read(self._settings.tool_names.list_windows, {})
        body = _object(_payload_json(payload))
        return tuple(
            _window(_object(entry)) for entry in _array(_required(body, "windows"), "windows")
        )

    def find_window(self, ref: WindowRef) -> WindowInfo | None:
        """Filter the listing by pid, then match the window id.

        The pid filter is the driver's own parameter, so this stays one narrow
        read rather than a full desktop enumeration.
        """
        payload = self._read(self._settings.tool_names.list_windows, {"pid": ref.pid})
        body = _object(_payload_json(payload))
        for entry in _array(_required(body, "windows"), "windows"):
            window = _window(_object(entry))
            if window.ref == ref:
                return window
        return None

    def capture(self, request: CaptureRequest) -> CaptureResult:
        payload = self._read(
            self._settings.tool_names.window_state,
            {
                "pid": request.ref.pid,
                "window_id": request.ref.window_id,
                "include_screenshot": request.include_screenshot,
                "max_elements": min(request.max_elements, self._settings.max_elements),
            },
        )
        body = _object(_payload_json(payload))
        elements = tuple(
            _element(_object(entry)) for entry in _array(body.get("elements", []), "elements")
        )
        return CaptureResult(
            elements=elements,
            screenshot=self._capture_screenshot(
                payload, body, requested=request.include_screenshot
            ),
        )

    def cursor_position(self) -> ScreenPoint:
        payload = self._read(self._settings.tool_names.cursor_position, {})
        return _point(_object(_payload_json(payload)))

    # --- mutating input ---------------------------------------------------

    def click(self, target: AddressedTarget, *, button: PointerButton, count: int) -> DriverResult:
        return self._act(
            self._settings.tool_names.click,
            {**_target_arguments(target), "button": button.value, "count": count},
        )

    def scroll(self, target: ActionTarget, *, command: ScrollCommand) -> DriverResult:
        return self._act(
            self._settings.tool_names.scroll,
            {
                **_target_arguments(target),
                "direction": command.direction.value,
                "amount": command.amount,
                "by": command.by.value,
            },
        )

    def type_text(self, text: str, *, target: AddressedTarget) -> DriverResult:
        return self._act(
            self._settings.tool_names.type_text, {**_target_arguments(target), "text": text}
        )

    def press_key(self, keystroke: Keystroke, *, target: ActionTarget) -> DriverResult:
        """A bare key press. Modifiers route to `hotkey` instead — they are
        separate operations in the driver, not one call with a flag."""
        return self._act(
            self._settings.tool_names.press_key,
            {**_target_arguments(target), "key": _key_name(keystroke.key)},
        )

    def hotkey(self, keystroke: Keystroke, *, target: ActionTarget) -> DriverResult:
        """A combination, sent as `keys: [modifiers..., key]` — the driver's
        documented order: modifiers first, exactly one non-modifier last."""
        keys: list[JsonValue] = [_modifier_name(modifier.value) for modifier in keystroke.modifiers]
        keys.append(_key_name(keystroke.key))
        return self._act(
            self._settings.tool_names.hotkey, {**_target_arguments(target), "keys": keys}
        )

    def bring_to_front(self, ref: WindowRef) -> DriverResult:
        return self._act(
            self._settings.tool_names.bring_to_front,
            {"pid": ref.pid, "window_id": ref.window_id},
        )

    # --- internals --------------------------------------------------------

    def _read(self, name: str, arguments: Mapping[str, JsonValue]) -> McpToolResult:
        """An observation. Strict: a reply Friday cannot parse is not data."""
        self._ensure_running()
        return self._transport.call_tool(name, arguments)

    def _act(self, name: str, arguments: Mapping[str, JsonValue]) -> DriverResult:
        """A mutation. Lenient about the reply body, never about failure.

        A tool error still raises — that path is the transport's. But a reply
        that carried only a text summary means "no structured detail", not "the
        action failed": the side effect has already happened, and reporting it
        as a driver failure would tell the caller the opposite of the truth.
        """
        self._ensure_running()
        result = self._transport.call_tool(name, arguments, require_payload=False)
        payload = _payload_json_or_none(result)
        if not isinstance(payload, dict):
            return DriverResult()
        effect = payload.get("effect")
        verified = payload.get("verified")
        return DriverResult(
            effect=effect if isinstance(effect, str) else None,
            verified=verified if isinstance(verified, bool) else None,
        )

    def _ensure_running(self) -> None:
        """`start()` is idempotent, so calling it here costs nothing on the
        happy path and removes a class of "driver used before preflight" bugs."""
        try:
            self._transport.start()
        except ComputerDriverUnavailable:
            raise
        except ComputerUseError as exc:
            raise ComputerDriverUnavailable("the computer-use driver is not running") from exc

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

    def _capture_screenshot(
        self,
        payload: McpToolResult,
        body: dict[str, JsonValue],
        *,
        requested: bool,
    ) -> Screenshot | None:
        """Join structured state with its sibling MCP image block.

        Cua puts AX elements in structured content and the PNG in an MCP image
        block. Keeping those sibling blocks intact is what lets a real capture
        contain both semantic and visual evidence.
        """
        if not payload.image_content:
            return self._screenshot(body.get("screenshot"))
        if len(payload.image_content) != 1:
            raise ComputerDriverFailed("the computer-use driver returned multiple capture images")
        if not requested:
            raise ComputerDriverFailed("the computer-use driver returned an unexpected image")
        return self._image_screenshot(payload.image_content[0], body.get("screenshot"))

    def _image_screenshot(self, image: McpImageContent, metadata: JsonValue) -> Screenshot:
        self._reject_oversized(image.data)
        try:
            data = base64.b64decode(image.data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ComputerDriverFailed(
                "the computer-use driver returned an undecodable image payload"
            ) from exc
        if len(data) > self._settings.max_capture_bytes:
            raise ComputerDriverFailed("the captured image exceeds the configured capture ceiling")
        if image.media_type != "image/png":
            raise ComputerDriverFailed("the computer-use driver returned a malformed media type")
        width, height = _image_dimensions(metadata, data)
        return Screenshot(data=data, media_type=image.media_type, width=width, height=height)

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


def _payload_json(result: McpToolResult) -> JsonValue:
    """Select JSON only in the driver adapter, never in the transport."""
    payload = _payload_json_or_none(result)
    if payload is None:
        raise ComputerDriverFailed("the computer-use driver returned no usable content")
    return payload


def _payload_json_or_none(result: McpToolResult) -> JsonValue | None:
    return (
        result.structured_content if result.structured_content is not None else result.text_content
    )


def _image_dimensions(metadata: JsonValue, data: bytes) -> tuple[int, int]:
    """Use Cua's structured metadata, with a PNG-header fallback.

    MCP image blocks carry bytes and MIME type but no width/height. Current
    Cua structured state can provide the dimensions; accepting a standard PNG
    IHDR as fallback keeps the adapter correct if that metadata is omitted.
    """
    if isinstance(metadata, dict) and "width" in metadata and "height" in metadata:
        return _int(metadata, "width"), _int(metadata, "height")
    if len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    raise ComputerDriverFailed("the computer-use driver returned image bytes without dimensions")


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


def _frame(body: dict[str, JsonValue]) -> PixelFrame:
    """An element frame, in window-local screenshot pixels.

    The driver spells the extents `w`/`h` here rather than `width`/`height`, and
    that difference is exactly why this is a separate reader from `_bounds`
    instead of a shared one with optional keys.
    """
    return PixelFrame(
        x=_int(body, "x"),
        y=_int(body, "y"),
        width=_int(body, "w"),
        height=_int(body, "h"),
    )


def _window(body: dict[str, JsonValue]) -> WindowInfo:
    try:
        ref = WindowRef(pid=_int(body, "pid"), window_id=_int(body, "window_id"))
    except ValueError as exc:
        raise ComputerDriverFailed(_MALFORMED) from exc
    return WindowInfo(
        ref=ref,
        title=_str(body, "title", default=""),
        bounds=_bounds(_object(_required(body, "bounds"))),
        app_name=_str(body, "app_name", default=""),
        z_index=_int(body, "z_index") if "z_index" in body else 0,
        is_on_screen=_bool(body, "is_on_screen", default=False),
        on_current_space=_bool(body, "on_current_space", default=False),
    )


def _element(body: dict[str, JsonValue]) -> CapturedElement:
    label = body.get("label")
    if label is not None and not isinstance(label, str):
        raise ComputerDriverFailed(_MALFORMED)
    token = body.get("element_token")
    if token is not None and not isinstance(token, str):
        raise ComputerDriverFailed(_MALFORMED)
    try:
        return CapturedElement(
            element_index=_int(body, "element_index"),
            role=_str(body, "role"),
            frame=_frame(_object(_required(body, "frame"))),
            label=label,
            element_token=token,
        )
    except ValueError as exc:
        # a driver-supplied role, label, or token its own value object rejects
        raise ComputerDriverFailed(_MALFORMED) from exc


def _target_arguments(target: ActionTarget) -> dict[str, JsonValue]:
    """Render a resolved target as the driver's addressing arguments.

    Element addressing carries `element_token` alongside the index when the
    driver supplied one: the token lets the driver itself detect a superseded
    snapshot and refuse, which is a second, independent check on the freshness
    Friday already established by capturing moments earlier.
    """
    if isinstance(target, ElementTarget):
        arguments: dict[str, JsonValue] = {
            "pid": target.ref.pid,
            "window_id": target.ref.window_id,
            "element_index": target.element_index,
        }
        if target.element_token is not None:
            arguments["element_token"] = target.element_token
        return arguments
    if isinstance(target, PixelTarget):
        return {
            "pid": target.ref.pid,
            "window_id": target.ref.window_id,
            "x": target.point.x,
            "y": target.point.y,
        }
    if isinstance(target, WindowTarget):
        return {"pid": target.ref.pid, "window_id": target.ref.window_id}
    raise ComputerDriverFailed(_MALFORMED)  # pragma: no cover - union is exhaustive


def _key_name(key: str) -> str:
    """Map one Friday key name onto the driver's vocabulary.

    Single characters pass through; named keys must be in the table. An unknown
    name is a refusal rather than a passthrough, because a name the driver does
    not recognize is a keystroke nobody can predict.
    """
    if len(key) == 1:
        return key
    mapped = _KEY_NAMES.get(key)
    if mapped is None:
        raise ComputerDriverFailed("the requested key has no driver equivalent")
    return mapped


def _modifier_name(modifier: str) -> str:
    mapped = _MODIFIER_NAMES.get(modifier)
    if mapped is None:  # pragma: no cover - KeyModifier is a closed enum
        raise ComputerDriverFailed("the requested modifier has no driver equivalent")
    return mapped
