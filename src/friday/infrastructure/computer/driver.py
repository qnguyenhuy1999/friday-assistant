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
return a DriverResult describing what was observed afterwards.

Implementations receive only already-validated, already-fenced inputs: an
absolute ScreenPoint that the gateway has confirmed lies inside a live
captured window, a Keystroke drawn from a closed allowlist, bounded text. A
driver is a transport, not a policy layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from friday.infrastructure.computer.models import (
    CaptureRequest,
    CaptureResult,
    DriverResult,
    Keystroke,
    PointerButton,
    PointerTarget,
    ScreenPoint,
    ScrollDelta,
    WindowInfo,
)


@dataclass(frozen=True, slots=True)
class ComputerDriverHealth:
    """Startup and preflight verdict. `detail` is for Friday's own logs and
    operator-facing preflight output — never forwarded to the brain."""

    available: bool
    detail: str = ""


class ComputerDriver(Protocol):
    def health(self) -> ComputerDriverHealth: ...

    # --- read-only observation -------------------------------------------

    def capture(self, request: CaptureRequest) -> CaptureResult: ...

    def pointer_position(self) -> ScreenPoint: ...

    def list_windows(self) -> tuple[WindowInfo, ...]: ...

    def active_window(self) -> WindowInfo | None: ...

    # --- mutating input ---------------------------------------------------

    def move_pointer(self, target: PointerTarget) -> DriverResult: ...

    def click(
        self, target: PointerTarget, *, button: PointerButton, count: int
    ) -> DriverResult: ...

    def scroll(self, target: PointerTarget, *, delta: ScrollDelta) -> DriverResult: ...

    def type_text(self, text: str, *, window_id: str | None) -> DriverResult: ...

    def press_keystroke(self, keystroke: Keystroke, *, window_id: str | None) -> DriverResult: ...

    def focus_window(self, window_id: str) -> DriverResult: ...
