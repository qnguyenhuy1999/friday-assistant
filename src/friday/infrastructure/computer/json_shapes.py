"""JSON shaping for observed desktop state.

The only place computer-use models become tool output. Keeping it separate
from the handlers means one reviewable answer to "what does Claude actually
get to see about the desktop?" — currently window identity, geometry, and
already-sanitized text, and nothing else. Notably absent: process ids, file
paths, and window contents.
"""

from __future__ import annotations

from friday.domain.json_value import JsonValue
from friday.infrastructure.computer.models import CapturedElement, ScreenBounds, WindowInfo


def bounds_json(bounds: ScreenBounds) -> dict[str, JsonValue]:
    return {
        "x": bounds.x,
        "y": bounds.y,
        "width": bounds.width,
        "height": bounds.height,
    }


def window_json(window: WindowInfo) -> dict[str, JsonValue]:
    """`title` and `application` are already control-stripped, whitespace-
    collapsed, and length-bounded by WindowInfo — this only reshapes."""
    return {
        "window_id": window.window_id,
        "title": window.title,
        "application": window.application,
        "is_active": window.is_active,
        "bounds": bounds_json(window.bounds),
    }


def element_json(element: CapturedElement) -> dict[str, JsonValue]:
    """`element_id` is snapshot-scoped, and the wire shape says nothing to
    suggest otherwise: there is no global handle here for Claude to cache and
    reuse against a later capture."""
    return {
        "element_id": element.element_id,
        "role": element.role,
        "label": element.label,
        "bounds": bounds_json(element.bounds),
    }
