"""JSON shaping for observed desktop state and completed actions.

The only place computer-use models become tool output. Keeping it separate from
the handlers means one reviewable answer to "what does Claude actually get to
see about the desktop?" — window identity, geometry, already-sanitized text,
and nothing else. Notably absent: file paths, window contents, and element
indices.

**Why `element_index` is not in the element shape.** The driver rebuilds its
index map on every capture, so an index Claude read here would address whatever
happens to occupy that slot by the time an approval clears. Publishing one
would be publishing a handle that looks stable and is not. Elements are
addressed by `role` + `label`; when those are ambiguous, `frame` gives the
pixels to address instead.

The index *does* appear in an action's result, which is the opposite case: that
is a record of what Friday already did, not a handle for what to do next.
"""

from __future__ import annotations

from friday.domain.json_value import JsonValue
from friday.infrastructure.computer.models import (
    CapturedElement,
    ElementTarget,
    PixelFrame,
    PixelTarget,
    ScreenBounds,
    ScreenSnapshot,
    WindowInfo,
    WindowTarget,
)


def bounds_json(bounds: ScreenBounds) -> dict[str, JsonValue]:
    """Desktop points: where the window sits on the desktop."""
    return {
        "x": bounds.x,
        "y": bounds.y,
        "width": bounds.width,
        "height": bounds.height,
    }


def frame_json(frame: PixelFrame) -> dict[str, JsonValue]:
    """Window-local screenshot pixels: the space actions are addressed in."""
    return {
        "x": frame.x,
        "y": frame.y,
        "width": frame.width,
        "height": frame.height,
    }


def window_json(window: WindowInfo) -> dict[str, JsonValue]:
    """`title` and `app_name` are already control-stripped, whitespace-
    collapsed, and length-bounded by WindowInfo — this only reshapes.

    `z_index`, `is_on_screen`, and `on_current_space` are reported as the raw
    facts they are. Friday does not fold them into an `active` flag: the
    frontmost window is derivable from them, but z-order is not proof of
    keyboard focus, and a boolean named `is_active` would claim it was.
    """
    return {
        "pid": window.pid,
        "window_id": window.window_id,
        "title": window.title,
        "app_name": window.app_name,
        "bounds": bounds_json(window.bounds),
        "z_index": window.z_index,
        "is_on_screen": window.is_on_screen,
        "on_current_space": window.on_current_space,
    }


def element_json(element: CapturedElement) -> dict[str, JsonValue]:
    """The addressable facts about one control: what it is, what it says, and
    where it is in the image. No index — see the module docstring."""
    return {
        "role": element.role,
        "label": element.label,
        "frame": frame_json(element.frame),
    }


def action_json(
    snapshot: ScreenSnapshot,
    target: ElementTarget | PixelTarget | WindowTarget,
    **extra: JsonValue,
) -> dict[str, JsonValue]:
    """Describe a completed action against the capture it was resolved on.

    This is what the durable ToolInvocation keeps, so it has to answer "what
    did Friday actually act on?" without the capture it was resolved against —
    hence the window title, the resolved element index, and the coordinates all
    being recorded rather than only the request.
    """
    output: dict[str, JsonValue] = {
        "pid": snapshot.window.pid,
        "window_id": snapshot.window.window_id,
        "window_title": snapshot.window.title,
        "capture_id": snapshot.capture_id.value,
        "target": _target_json(target),
    }
    output.update(extra)
    return output


def _target_json(target: ElementTarget | PixelTarget | WindowTarget) -> dict[str, JsonValue]:
    if isinstance(target, ElementTarget):
        return {
            "kind": "element",
            "role": target.descriptor.role,
            "label": target.descriptor.label,
            "element_index": target.element_index,
        }
    if isinstance(target, PixelTarget):
        return {"kind": "pixel", "x": target.point.x, "y": target.point.y}
    return {"kind": "window"}


def driver_effect_json(effect: str | None, verified: bool | None) -> dict[str, JsonValue]:
    """Pass the driver's own confidence through unchanged.

    A click cannot be confirmed by read-back, and a driver that says so is
    being honest, not failing. Reporting `verified: false` is what tells Claude
    to confirm with a fresh capture instead of assuming the action landed;
    flattening it into success would manufacture a certainty nothing has.
    """
    return {"effect": effect, "verified": verified}
