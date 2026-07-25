"""Snapshot fencing and target resolution.

The single place a proposed mutation turns into a concrete point on a screen,
and therefore the single place that decision can be reviewed. Every mutating
computer tool passes through `resolve_fence`; the pointer ones also pass
through `resolve_pointer_target`.

The driver is never consulted. It receives a `PointerTarget` that has already
been proven to lie inside a window Friday itself observed, within the TTL,
under the right Run. A driver that did its own snapshot lookup would be a
second fence implementation, and the two would eventually disagree — at which
point the safety argument becomes "whichever one ran".

`element` and `x`/`y` are exclusive rather than merged with a precedence rule.
A call carrying both is a call whose author is confused about where the click
lands, and there is no reading of it that is safe to guess.
"""

from __future__ import annotations

from datetime import datetime

from friday.domain.json_value import JsonValue
from friday.infrastructure.computer.errors import (
    SnapshotMismatch,
    TargetInvalid,
    TargetOutOfBounds,
)
from friday.infrastructure.computer.models import (
    MAX_WINDOW_ID_CHARS,
    PointerTarget,
    ScreenPoint,
    ScreenSnapshot,
)
from friday.infrastructure.computer.snapshots import SnapshotRegistry
from friday.infrastructure.computer.tool_input import optional_int, required_str

FENCE_FIELDS: frozenset[str] = frozenset({"snapshot_id", "window_id"})
"""Required on every mutating computer tool, without exception."""

TARGET_FIELDS: frozenset[str] = frozenset({"element", "x", "y"})

MAX_SNAPSHOT_ID_CHARS = 64
"""A well-formed id is 35 characters; the slack only exists so an obviously
malformed value is reported as an unknown fence rather than as a length error."""


def resolve_fence(
    values: dict[str, JsonValue],
    *,
    registry: SnapshotRegistry,
    run_scope: str,
    now: datetime,
) -> ScreenSnapshot:
    """Validate the {snapshot_id, window_id} fence and return the live capture.

    Requiring `window_id` alongside `snapshot_id` is not redundancy: it makes
    Claude state which window it believes it is acting on, so a capture that
    silently resolved to a different window than intended is caught here
    instead of at the moment of the click.
    """
    snapshot_id = required_str(values, "snapshot_id", max_chars=MAX_SNAPSHOT_ID_CHARS)
    window_id = required_str(values, "window_id", max_chars=MAX_WINDOW_ID_CHARS)
    snapshot = registry.resolve(snapshot_id, run_scope=run_scope, now=now)
    if snapshot.window.window_id != window_id:
        raise SnapshotMismatch("window_id does not match the window in the cited capture")
    return snapshot


def resolve_pointer_target(snapshot: ScreenSnapshot, values: dict[str, JsonValue]) -> PointerTarget:
    """Turn `element` or `x`/`y` into an already-fenced absolute point."""
    element_id = optional_int(values, "element")
    x = optional_int(values, "x")
    y = optional_int(values, "y")

    if element_id is not None and (x is not None or y is not None):
        raise TargetInvalid("supply either 'element' or 'x' and 'y', never both")
    if element_id is None and x is None and y is None:
        raise TargetInvalid("supply either 'element' or both 'x' and 'y'")

    point = (
        _element_point(snapshot, element_id)
        if element_id is not None
        else _coordinate_point(x, y, snapshot)
    )
    return PointerTarget(point=point, window_id=snapshot.window.window_id)


def _element_point(snapshot: ScreenSnapshot, element_id: int) -> ScreenPoint:
    """An element id is meaningful only inside its own snapshot: `14` is not a
    desktop-wide handle, and resolving it globally is exactly the bug this
    lookup exists to prevent."""
    element = snapshot.element(element_id)
    if element is None:
        raise SnapshotMismatch("element is not part of the cited capture")
    point = element.bounds.center
    if not snapshot.window.bounds.contains(point):
        # the driver reported an element outside the window it captured;
        # untrusted output, so refuse rather than reconcile
        raise TargetOutOfBounds("the captured element does not lie inside the captured window")
    return point


def _coordinate_point(x: int | None, y: int | None, snapshot: ScreenSnapshot) -> ScreenPoint:
    if x is None or y is None:
        raise TargetInvalid("supply both 'x' and 'y', or use 'element' instead")
    try:
        point = ScreenPoint(x=x, y=y)
    except ValueError:
        # magnitude beyond MAX_COORDINATE — out of bounds by construction
        raise TargetOutOfBounds("coordinate is outside the addressable screen range") from None
    if not snapshot.window.bounds.contains(point):
        raise TargetOutOfBounds("coordinate lies outside the bounds of the captured window")
    return point
