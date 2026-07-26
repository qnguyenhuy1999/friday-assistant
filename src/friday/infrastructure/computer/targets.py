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
    TargetAmbiguous,
    TargetInvalid,
    TargetNotFound,
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
    ScreenSnapshot,
    WindowRef,
    WindowTarget,
)
from friday.infrastructure.computer.tool_input import (
    required_bounded_int,
    required_nested_object,
    required_str,
)

IDENTITY_FIELDS: frozenset[str] = frozenset({"pid", "window_id"})
"""Required on every mutating computer tool, without exception. Both halves:
the driver resolves elements per (pid, window_id), and a pid alone names an
application rather than a surface."""

TARGET_FIELDS: frozenset[str] = frozenset({"element"})
"""A delayed mutation names a semantic element, never a raw screenshot pixel.

The screenshot can change while an approval is waiting.  Re-capturing only
proves that a coordinate is in bounds, not that it is still the approved
control; an unchanged coordinate may now be Delete rather than Continue.
Pixel addressing remains an adapter capability for a future visual-equivalence
fence, but it is intentionally not reachable through Friday's tool surface.
"""

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
) -> ElementDescriptor | None:
    """Read the approved semantic target, if this operation needs one."""
    if "element" in values:
        return _parse_descriptor(values)
    if required:
        raise TargetInvalid("supply an 'element' with its role and label")
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
        return self._capture(ref, now=now)

    def _resolve(
        self, values: dict[str, JsonValue], *, now: datetime, target_required: bool
    ) -> tuple[ScreenSnapshot, ActionTarget]:
        ref = parse_window_ref(values)
        request = parse_target_request(values, required=target_required)
        snapshot = self._capture(ref, now=now)
        if request is None:
            return snapshot, WindowTarget(ref=ref)
        return snapshot, self._element_target(snapshot, request)

    def _capture(self, ref: WindowRef, *, now: datetime) -> ScreenSnapshot:
        """Look at the named window, right now.

        The window's existence is checked against `list_windows` first so that
        "the window is gone" is reported as itself rather than as a generic
        driver failure — the two call for different responses from Claude, and
        only one of them is worth re-proposing against.

        Target resolution needs the accessibility tree, not image pixels. The
        image belongs to explicit `computer.capture` observations.
        """
        window = self._driver.find_window(ref)
        if window is None:
            raise WindowGone("that window no longer exists; list the windows again")
        result = self._driver.capture(
            CaptureRequest(
                ref=ref,
                include_screenshot=False,
                max_elements=self._settings.max_elements,
            )
        )
        return ScreenSnapshot(
            capture_id=CaptureId.new(),
            captured_at=now,
            window=window,
            elements=result.elements,
            extent=None,
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
                "wait for an unambiguous semantic descriptor before proposing again"
            )
        element = matches[0]
        return ElementTarget(
            ref=snapshot.window.ref,
            element_index=element.element_index,
            descriptor=descriptor,
            element_token=element.element_token,
        )
