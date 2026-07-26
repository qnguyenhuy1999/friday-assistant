"""The ComputerDriver port: the single seam between Friday's safety policy
and whatever actually moves a pointer.

Placement is deliberate. This protocol is infrastructure-internal, reachable
only from friday.infrastructure.computer and the ComputerToolGateway that
owns it. The brain runtime, AgentRunProcessor, and WorkerLoop must not import
it, construct it, or know it exists — the only path from a proposed action to
a desktop side effect stays:

    ExecuteToolAction -> ToolGateway -> ComputerToolGateway -> ComputerDriver

Every method is synchronous and raises ComputerUseError subclasses on
failure, so a driver never returns a half-successful result the gateway would
have to interpret. Read operations return observations; mutating operations
return a DriverResult describing what the driver could and could not confirm.

Implementations receive only already-validated, already-fenced inputs: a
target resolved against a capture taken moments earlier in the same tool call,
a Keystroke drawn from a closed allowlist, bounded text. A driver is a
transport, not a policy layer — it never resolves an element, never picks a
window, and never decides whether an action is in bounds.

The method set is deliberately narrow and matches what the backing driver
actually offers. There is no `move_pointer`: pointer motion without a click
has no faithful backing (the driver's cursor move is an overlay in window
scope, and the real pointer only moves in desktop scope, which Friday never
captures) and nothing needs it — click, scroll, and type address their target
directly. There is no `active_window` either: "frontmost" is derivable from
`list_windows`, and a driver method claiming to report the *focused* window
would be asserting keyboard-focus ownership that z-order does not establish.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from friday.infrastructure.computer.models import (
    ActionTarget,
    AddressedTarget,
    CaptureRequest,
    CaptureResult,
    DriverResult,
    Keystroke,
    PointerButton,
    ScreenPoint,
    ScrollCommand,
    WindowInfo,
    WindowRef,
)


@dataclass(frozen=True, slots=True)
class ComputerDriverHealth:
    """Startup and preflight verdict. `detail` is for Friday's own logs and
    operator-facing preflight output — never forwarded to the brain."""

    available: bool
    detail: str = ""


class ComputerDriver(Protocol):
    # --- lifecycle --------------------------------------------------------

    def health(self) -> ComputerDriverHealth: ...

    def close(self) -> None:
        """Release the driver process and its streams.

        Part of the port rather than an implementation detail: the gateway that
        owns a driver is responsible for shutting it down, and a protocol
        without this leaves every composition root free to leak one.
        """
        ...

    # --- read-only observation -------------------------------------------

    def list_windows(self) -> tuple[WindowInfo, ...]: ...

    def find_window(self, ref: WindowRef) -> WindowInfo | None:
        """Look up one window's metadata, or None if it is gone.

        Separate from `capture` because the two answer different questions
        against different backing operations: this one is a cheap listing read,
        `capture` walks the window's contents. Returning None rather than
        raising keeps "the window went away" a fact the caller can report as
        itself instead of a failure it has to interpret.
        """
        ...

    def capture(self, request: CaptureRequest) -> CaptureResult: ...

    def cursor_position(self) -> ScreenPoint:
        """The OS cursor position, in desktop points — *not* in any window's
        screenshot pixel space, and therefore never usable as a click target."""
        ...

    # --- mutating input ---------------------------------------------------

    def click(
        self, target: AddressedTarget, *, button: PointerButton, count: int
    ) -> DriverResult: ...

    def scroll(self, target: ActionTarget, *, command: ScrollCommand) -> DriverResult:
        """Scroll at a target, or drive the window's focused scroller.

        Accepts a window-level target because the driver draws that distinction
        itself: addressed at a point it synthesizes a wheel event there, which
        is the only way to reach a nested scrollable region; unaddressed it
        drives the focused scroller by keystroke.
        """
        ...

    def type_text(self, text: str, *, target: AddressedTarget) -> DriverResult: ...

    def press_key(self, keystroke: Keystroke, *, target: ActionTarget) -> DriverResult: ...

    def hotkey(self, keystroke: Keystroke, *, target: ActionTarget) -> DriverResult: ...

    def bring_to_front(self, ref: WindowRef) -> DriverResult:
        """Raise a window to the foreground.

        Named for what it does. This genuinely steals foreground from whatever
        the user was in — it is not "focus a window" bookkeeping — so the name
        has to carry that, both here and in the tool Claude sees.
        """
        ...
