"""FakeComputerDriver: an in-memory ComputerDriver for every layer above it.

This is the driver the safety tests run against. Its whole job is to make
"did a side effect actually reach the desktop?" observable: every call is
appended to `calls`, so a test can assert that an unapproved or unfenced
action produced *no* mutating call at all — the difference between a gateway
that rejects and a gateway that merely reports rejection.

It is deliberately not a src-side dependency. Production wiring never
substitutes a fake for a real driver; when computer use is disabled the tools
are not registered at all (fail closed, no no-op driver).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from friday.infrastructure.computer.driver import ComputerDriverHealth
from friday.infrastructure.computer.errors import (
    ComputerDriverFailed,
    ComputerDriverUnavailable,
)
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

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake"

READ_ONLY_CALLS = frozenset({"capture", "pointer_position", "list_windows", "active_window"})


@dataclass(frozen=True, slots=True)
class DriverCall:
    """One recorded driver interaction. `payload` holds the typed arguments,
    so assertions can check exactly what reached the desktop."""

    name: str
    payload: tuple[tuple[str, object], ...] = ()

    def argument(self, key: str) -> object:
        return dict(self.payload)[key]


def default_window(window_id: str = "win-mail", *, is_active: bool = True) -> WindowInfo:
    return WindowInfo(
        window_id=window_id,
        title="Mail",
        bounds=ScreenBounds(x=0, y=0, width=1000, height=800),
        application="Mail",
        is_active=is_active,
    )


def default_elements() -> tuple[CapturedElement, ...]:
    return (
        CapturedElement(
            element_id=14,
            role="text_field",
            bounds=ScreenBounds(x=100, y=50, width=200, height=30),
            label="Search",
        ),
        CapturedElement(
            element_id=15,
            role="button",
            bounds=ScreenBounds(x=320, y=50, width=80, height=30),
            label="Send",
        ),
    )


def default_screenshot() -> Screenshot:
    return Screenshot(data=PNG_BYTES, media_type="image/png", width=1000, height=800)


@dataclass(slots=True)
class FakeComputerDriver:
    """Scriptable ComputerDriver. Mutable by design — tests reconfigure it
    between steps to simulate a desktop changing under Friday's feet."""

    windows: tuple[WindowInfo, ...] = field(default_factory=lambda: (default_window(),))
    elements: tuple[CapturedElement, ...] = field(default_factory=default_elements)
    pointer: ScreenPoint = field(default_factory=lambda: ScreenPoint(x=0, y=0))
    screenshot: Screenshot | None = field(default_factory=default_screenshot)
    available: bool = True
    health_detail: str = "fake driver ready"
    # any Exception, not just ComputerUseError: the gateway must also be
    # provable against raw OSErrors escaping a real driver
    raises: Exception | None = None
    calls: list[DriverCall] = field(default_factory=list)
    on_call: Callable[[str], None] | None = None
    """Fires after a call is recorded, so a test can act at the exact moment a
    side effect has landed but Friday has not yet persisted its outcome — the
    claim-loss window that makes a desktop action ambiguous. A hook rather than
    method patching because this dataclass uses slots."""

    # --- inspection helpers used by tests ---------------------------------

    @property
    def call_names(self) -> tuple[str, ...]:
        return tuple(call.name for call in self.calls)

    @property
    def mutating_calls(self) -> tuple[DriverCall, ...]:
        return tuple(call for call in self.calls if call.name not in READ_ONLY_CALLS)

    def only_call(self, name: str) -> DriverCall:
        matching = [call for call in self.calls if call.name == name]
        if len(matching) != 1:
            raise AssertionError(f"expected exactly one {name!r} call, got {len(matching)}")
        return matching[0]

    # --- ComputerDriver ---------------------------------------------------

    def health(self) -> ComputerDriverHealth:
        return ComputerDriverHealth(available=self.available, detail=self.health_detail)

    def capture(self, request: CaptureRequest) -> CaptureResult:
        self._record("capture", window_id=request.window_id)
        window = self._resolve_window(request.window_id)
        elements = self.elements[: request.max_elements] if request.include_elements else ()
        return CaptureResult(
            window=window,
            elements=elements,
            screenshot=self.screenshot if request.include_screenshot else None,
        )

    def pointer_position(self) -> ScreenPoint:
        self._record("pointer_position")
        return self.pointer

    def list_windows(self) -> tuple[WindowInfo, ...]:
        self._record("list_windows")
        return self.windows

    def active_window(self) -> WindowInfo | None:
        self._record("active_window")
        return next((window for window in self.windows if window.is_active), None)

    def move_pointer(self, target: PointerTarget) -> DriverResult:
        self._record("move_pointer", point=target.point, window_id=target.window_id)
        self.pointer = target.point
        return DriverResult(pointer_position=target.point, window_id=target.window_id)

    def click(self, target: PointerTarget, *, button: PointerButton, count: int) -> DriverResult:
        self._record(
            "click", point=target.point, window_id=target.window_id, button=button, count=count
        )
        self.pointer = target.point
        return DriverResult(pointer_position=target.point, window_id=target.window_id)

    def scroll(self, target: PointerTarget, *, delta: ScrollDelta) -> DriverResult:
        self._record("scroll", point=target.point, window_id=target.window_id, delta=delta)
        return DriverResult(pointer_position=target.point, window_id=target.window_id)

    def type_text(self, text: str, *, window_id: str | None) -> DriverResult:
        self._record("type_text", text=text, window_id=window_id)
        return DriverResult(window_id=window_id)

    def press_keystroke(self, keystroke: Keystroke, *, window_id: str | None) -> DriverResult:
        self._record("press_keystroke", keystroke=keystroke, window_id=window_id)
        return DriverResult(window_id=window_id)

    def focus_window(self, window_id: str) -> DriverResult:
        self._record("focus_window", window_id=window_id)
        self._resolve_window(window_id)
        self.windows = tuple(
            WindowInfo(
                window_id=window.window_id,
                title=window.title,
                bounds=window.bounds,
                application=window.application,
                is_active=window.window_id == window_id,
            )
            for window in self.windows
        )
        return DriverResult(window_id=window_id)

    # --- internals --------------------------------------------------------

    def _record(self, name: str, **payload: object) -> None:
        if not self.available:
            raise ComputerDriverUnavailable(f"fake driver is unavailable: {name}")
        if self.raises is not None:
            raise self.raises
        self.calls.append(DriverCall(name=name, payload=tuple(sorted(payload.items()))))
        if self.on_call is not None:
            self.on_call(name)

    def _resolve_window(self, window_id: str | None) -> WindowInfo:
        if window_id is None:
            active = next((window for window in self.windows if window.is_active), None)
            if active is None:
                raise ComputerDriverFailed("no active window")
            return active
        for window in self.windows:
            if window.window_id == window_id:
                return window
        raise ComputerDriverFailed(f"unknown window: {window_id}")
