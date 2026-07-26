"""Target resolution: where an approved intention becomes a concrete place on
a screen.

Every mutating computer tool passes through here, and the sequence is always
the same:

    name the window  ->  capture it NOW  ->  find the approved target in that
    capture  ->  hand the driver something it cannot misread

**Why the capture happens here, at execution time.** An approval is granted by
a human, and humans take seconds or minutes. A worker can also hand the resumed
Run to a different process than the one that observed the desktop. So a fence
built out of "the capture Claude cited earlier" fails in two ways that have
nothing to do with safety: it expires while someone reads the request, and it
vanishes when another worker picks the run up. Both failures then re-enter
approval, and the loop can repeat indefinitely.

The fix is to stop treating a past observation as the fence. What the approval
binds is the *intention* — this window, this control, by role and label — and
what makes the action safe is that Friday looks again immediately before acting
and refuses unless the intention still resolves, uniquely, in what it now sees.
Approval evidence and execution freshness are two different jobs, and one
identifier cannot hold both.

Consequences worth stating plainly:

* An element index is never accepted from Claude. The driver rebuilds its index
  map on every capture, so an index from an earlier one addresses whatever now
  occupies that slot. Friday resolves the index itself, from the capture it
  just took, and it never leaves this module.
* A descriptor matching two controls is refused (`TargetAmbiguous`), not
  resolved by taking the first. "The button labelled Send" does not authorize a
  guess about which Send.
* The residual risk is real and bounded: if the application has replaced the
  approved control with a *different* control of the same role and label in the
  same window, this passes. Window identity plus uniqueness is the fence.
  Tolerance-matching the old frame would tighten it but would refuse every
  legitimate window move or resize — trading a rare wrong click for a common
  false refusal, which is the worse bargain.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from friday.domain.json_value import JsonValue
from friday.infrastructure.computer.driver import ComputerDriver
from friday.infrastructure.computer.errors import (
    ComputerDriverFailed,
    TargetAmbiguous,
    TargetInvalid,
    TargetNotFound,
    TargetOutOfBounds,
    WindowGone,
)
from friday.infrastructure.computer.models import (
    DEFAULT_MAX_ELEMENTS,
    MAX_ELEMENTS_CEILING,
    MAX_LABEL_CHARS,
    MAX_PID,
    MAX_ROLE_CHARS,
    MAX_WINDOW_ID,
    ActionTarget,
    AddressedTarget,
    CaptureId,
    CaptureRequest,
    ElementDescriptor,
    ElementTarget,
    PixelPoint,
    PixelTarget,
    ScreenSnapshot,
    WindowRef,
    WindowTarget,
)
from friday.infrastructure.computer.tool_input import (
    optional_int,
    required_bounded_int,
    required_nested_object,
    required_str,
)

IDENTITY_FIELDS: frozenset[str] = frozenset({"pid", "window_id"})
"""Required on every mutating computer tool, without exception. Both halves:
the driver resolves elements per (pid, window_id), and a pid alone names an
application rather than a surface."""

TARGET_FIELDS: frozenset[str] = frozenset({"element", "x", "y"})

ELEMENT_FIELDS: frozenset[str] = frozenset({"role", "label"})


@dataclass(frozen=True, slots=True)
class TargetResolverSettings:
    max_elements: int = DEFAULT_MAX_ELEMENTS

    def __post_init__(self) -> None:
        if self.max_elements < 1 or self.max_elements > MAX_ELEMENTS_CEILING:
            raise ValueError(
                f"TargetResolverSettings.max_elements must be between 1 and {MAX_ELEMENTS_CEILING}"
            )


def parse_window_ref(values: dict[str, JsonValue]) -> WindowRef:
    """Read the `(pid, window_id)` identity an action claims to be acting on."""
    pid = required_bounded_int(values, "pid", maximum=MAX_PID)
    window_id = required_bounded_int(values, "window_id", maximum=MAX_WINDOW_ID)
    return WindowRef(pid=pid, window_id=window_id)


def parse_target_request(
    values: dict[str, JsonValue], *, required: bool
) -> ElementDescriptor | PixelPoint | None:
    """Read `element` or `x`/`y` — never both, and never a merge of the two.

    A call carrying both is a call whose author is confused about where the
    action lands, and there is no reading of it that is safe to guess.
    """
    has_element = "element" in values
    x = optional_int(values, "x")
    y = optional_int(values, "y")
    has_pixel = x is not None or y is not None

    if has_element and has_pixel:
        raise TargetInvalid("supply either 'element' or 'x' and 'y', never both")
    if has_element:
        return _parse_descriptor(values)
    if has_pixel:
        return _parse_pixel(x, y)
    if required:
        raise TargetInvalid("supply either 'element' or both 'x' and 'y'")
    return None


def _parse_descriptor(values: dict[str, JsonValue]) -> ElementDescriptor:
    """Read the stable handle: what the control *is*, not where it was.

    `label` is required rather than optional. A role on its own ("button")
    matches half a window, so an approval carrying only a role would authorize
    an action against any of them; requiring the label is what makes the
    uniqueness check meaningful.
    """
    element = required_nested_object(values, "element", allowed=ELEMENT_FIELDS)
    role = required_str(element, "role", max_chars=MAX_ROLE_CHARS)
    label = required_str(element, "label", max_chars=MAX_LABEL_CHARS)
    try:
        return ElementDescriptor(role=role, label=label)
    except ValueError as exc:
        raise TargetInvalid(str(exc)) from None


def _parse_pixel(x: int | None, y: int | None) -> PixelPoint:
    if x is None or y is None:
        raise TargetInvalid("supply both 'x' and 'y', or use 'element' instead")
    try:
        return PixelPoint(x=x, y=y)
    except ValueError:
        raise TargetOutOfBounds(
            "coordinates are window-local screenshot pixels and cannot be negative "
            "or beyond the addressable range"
        ) from None


class TargetResolver:
    """Captures the named window and resolves the approved target against it.

    Holds the driver because resolution *is* an observation: there is no way to
    answer "does this target still exist?" without looking, and splitting the
    look from the check would leave a window in which the answer goes stale.
    """

    def __init__(self, driver: ComputerDriver, settings: TargetResolverSettings | None = None):
        self._driver = driver
        self._settings = settings or TargetResolverSettings()

    def resolve_addressed(
        self, values: dict[str, JsonValue], *, now: datetime
    ) -> tuple[ScreenSnapshot, AddressedTarget]:
        """For actions that must name a place: click, scroll, type_text."""
        snapshot, target = self._resolve(values, now=now, target_required=True)
        assert not isinstance(target, WindowTarget)  # target_required rules it out
        return snapshot, target

    def resolve_any(
        self, values: dict[str, JsonValue], *, now: datetime
    ) -> tuple[ScreenSnapshot, ActionTarget]:
        """For actions a window can legitimately receive whole: press_key, hotkey."""
        return self._resolve(values, now=now, target_required=False)

    def resolve_window(self, values: dict[str, JsonValue], *, now: datetime) -> ScreenSnapshot:
        """For actions addressed at the window itself: bring_to_front.

        Still captures. Raising a window that no longer exists is harmless but
        untruthful, and the capture is what lets the result report the title of
        the thing that was actually raised.
        """
        ref = parse_window_ref(values)
        return self._capture(ref, need_extent=False, now=now)

    def _resolve(
        self, values: dict[str, JsonValue], *, now: datetime, target_required: bool
    ) -> tuple[ScreenSnapshot, ActionTarget]:
        ref = parse_window_ref(values)
        request = parse_target_request(values, required=target_required)
        snapshot = self._capture(ref, need_extent=isinstance(request, PixelPoint), now=now)
        if request is None:
            return snapshot, WindowTarget(ref=ref)
        if isinstance(request, PixelPoint):
            return snapshot, self._pixel_target(snapshot, request)
        return snapshot, self._element_target(snapshot, request)

    def _capture(self, ref: WindowRef, *, need_extent: bool, now: datetime) -> ScreenSnapshot:
        """Look at the named window, right now.

        The window's existence is checked against `list_windows` first so that
        "the window is gone" is reported as itself rather than as a generic
        driver failure — the two call for different responses from Claude, and
        only one of them is worth re-proposing against.

        The screenshot is only pulled when a pixel target needs its extent:
        for an element target the image is dead weight, and the driver
        documents the tree-only path as the cheap one.
        """
        window = self._driver.find_window(ref)
        if window is None:
            raise WindowGone("that window no longer exists; list the windows again")
        result = self._driver.capture(
            CaptureRequest(
                ref=ref,
                include_screenshot=need_extent,
                max_elements=self._settings.max_elements,
            )
        )
        extent = result.screenshot.extent if result.screenshot is not None else None
        if need_extent and extent is None:
            raise ComputerDriverFailed(
                "the driver returned no image, so a pixel target cannot be bounded"
            )
        return ScreenSnapshot(
            capture_id=CaptureId.new(),
            captured_at=now,
            window=window,
            elements=result.elements,
            extent=extent,
        )

    def _element_target(
        self, snapshot: ScreenSnapshot, descriptor: ElementDescriptor
    ) -> ElementTarget:
        matches = snapshot.matching(descriptor)
        if not matches:
            raise TargetNotFound(
                f"no {descriptor} is present in that window now; capture it again and re-propose"
            )
        if len(matches) > 1:
            raise TargetAmbiguous(
                f"{len(matches)} controls in that window match {descriptor}; "
                "address the one you mean by its x/y instead"
            )
        element = matches[0]
        return ElementTarget(
            ref=snapshot.window.ref,
            element_index=element.element_index,
            descriptor=descriptor,
            element_token=element.element_token,
        )

    def _pixel_target(self, snapshot: ScreenSnapshot, point: PixelPoint) -> PixelTarget:
        extent = snapshot.extent
        if extent is None:  # pragma: no cover - _capture guarantees it
            raise ComputerDriverFailed("the driver returned no image to bound a pixel target")
        if not extent.contains(point):
            raise TargetOutOfBounds(
                "that coordinate lies outside the current image of the window "
                f"({extent.width}x{extent.height} pixels)"
            )
        return PixelTarget(ref=snapshot.window.ref, point=point)
