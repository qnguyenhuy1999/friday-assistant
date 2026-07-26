"""FakeComputerDriver: an in-memory ComputerDriver for every layer above it.

This is the driver the safety tests run against. Its whole job is to make
"did a side effect actually reach the desktop?" observable: every call is
appended to `calls`, so a test can assert that an unapproved or unfenced
action produced *no* mutating call at all — the difference between a gateway
that rejects and a gateway that merely reports rejection.

It models the two things that make the real driver's contract load-bearing:

* **`(pid, window_id)` identity.** A window is addressed by both halves, and an
  unknown pair is simply absent — `find_window` returns None, which is how "the
  window went away between approval and execution" is simulated.
* **Per-capture element indices.** `capture` renumbers its elements on every
  call, exactly as the real driver replaces its index map. A test that assumed a
  stable index would fail here, which is the point: nothing in Friday may hold
  one across captures.

It is deliberately not a src-side dependency. Production wiring never
substitutes a fake for a real driver; when computer use is disabled the tools
are not registered at all (fail closed, no no-op driver).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from friday.infrastructure.computer.driver import ComputerDriverHealth
from friday.infrastructure.computer.errors import ComputerDriverUnavailable
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

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake"

MAIL_PID = 844
MAIL_WINDOW_ID = 10725
OTHER_WINDOW_ID = 20880

READ_ONLY_CALLS = frozenset({"list_windows", "find_window", "capture", "cursor_position"})


def mail_ref() -> WindowRef:
    return WindowRef(pid=MAIL_PID, window_id=MAIL_WINDOW_ID)


@dataclass(frozen=True, slots=True)
class DriverCall:
    """One recorded driver interaction. `payload` holds the typed arguments,
    so assertions can check exactly what reached the desktop."""

    name: str
    payload: tuple[tuple[str, object], ...] = ()

    def argument(self, key: str) -> object:
        return dict(self.payload)[key]


def default_window(
    window_id: int = MAIL_WINDOW_ID, *, pid: int = MAIL_PID, title: str = "Mail", z_index: int = 5
) -> WindowInfo:
    return WindowInfo(
        ref=WindowRef(pid=pid, window_id=window_id),
        title=title,
        bounds=ScreenBounds(x=0, y=0, width=1000, height=800),
        app_name="Mail",
        z_index=z_index,
        is_on_screen=True,
        on_current_space=True,
    )


def default_elements() -> tuple[CapturedElement, ...]:
    return (
        CapturedElement(
            element_index=14,
            role="text_field",
            frame=PixelFrame(x=100, y=50, width=200, height=30),
            label="Search",
            element_token="tok-search",
        ),
        CapturedElement(
            element_index=15,
            role="button",
            frame=PixelFrame(x=320, y=50, width=80, height=30),
            label="Send",
            element_token="tok-send",
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
    cursor: ScreenPoint = field(default_factory=lambda: ScreenPoint(x=0, y=0))
    screenshot: Screenshot | None = field(default_factory=default_screenshot)
    available: bool = True
    health_detail: str = "fake driver ready"
    closed: bool = False
    index_offset: int = 0
    """Added to every element index on each capture, so a test can prove Friday
    resolves indices from the capture it just took rather than reusing one."""
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

    def forget_window(self, ref: WindowRef | None = None) -> None:
        """Make a window disappear, as an application quitting would."""
        target = ref or mail_ref()
        self.windows = tuple(window for window in self.windows if window.ref != target)

    # --- ComputerDriver ---------------------------------------------------

    def health(self) -> ComputerDriverHealth:
        return ComputerDriverHealth(available=self.available, detail=self.health_detail)

    def close(self) -> None:
        self.closed = True

    def list_windows(self) -> tuple[WindowInfo, ...]:
        self._record("list_windows")
        return self.windows

    def find_window(self, ref: WindowRef) -> WindowInfo | None:
        self._record("find_window", ref=ref)
        return next((window for window in self.windows if window.ref == ref), None)

    def capture(self, request: CaptureRequest) -> CaptureResult:
        self._record("capture", ref=request.ref, include_screenshot=request.include_screenshot)
        elements = tuple(
            replace(element, element_index=element.element_index + self.index_offset)
            for element in self.elements[: request.max_elements]
        )
        return CaptureResult(
            elements=elements,
            screenshot=self.screenshot if request.include_screenshot else None,
        )

    def cursor_position(self) -> ScreenPoint:
        self._record("cursor_position")
        return self.cursor

    def click(self, target: AddressedTarget, *, button: PointerButton, count: int) -> DriverResult:
        self._record("click", target=target, button=button, count=count)
        return DriverResult(effect="unverifiable", verified=False)

    def scroll(self, target: ActionTarget, *, command: ScrollCommand) -> DriverResult:
        self._record("scroll", target=target, command=command)
        return DriverResult(effect="unverifiable", verified=False)

    def type_text(self, text: str, *, target: AddressedTarget) -> DriverResult:
        self._record("type_text", text=text, target=target)
        return DriverResult(effect="inserted", verified=True)

    def press_key(self, keystroke: Keystroke, *, target: ActionTarget) -> DriverResult:
        self._record("press_key", keystroke=keystroke, target=target)
        return DriverResult(effect="unverifiable", verified=False)

    def hotkey(self, keystroke: Keystroke, *, target: ActionTarget) -> DriverResult:
        self._record("hotkey", keystroke=keystroke, target=target)
        return DriverResult(effect="unverifiable", verified=False)

    def bring_to_front(self, ref: WindowRef) -> DriverResult:
        self._record("bring_to_front", ref=ref)
        self.windows = tuple(
            replace(window, z_index=99 if window.ref == ref else 1) for window in self.windows
        )
        return DriverResult(effect="fronted", verified=True)

    # --- internals --------------------------------------------------------

    def _record(self, name: str, **payload: object) -> None:
        if not self.available:
            raise ComputerDriverUnavailable(f"fake driver is unavailable: {name}")
        if self.raises is not None:
            raise self.raises
        self.calls.append(DriverCall(name=name, payload=tuple(sorted(payload.items()))))
        if self.on_call is not None:
            self.on_call(name)


def element_target(
    *, role: str = "button", label: str = "Send", index: int = 15, token: str | None = "tok-send"
) -> ElementTarget:
    """The resolved target a click on the default Send button produces."""
    from friday.infrastructure.computer.models import ElementDescriptor

    return ElementTarget(
        ref=mail_ref(),
        element_index=index,
        descriptor=ElementDescriptor(role=role, label=label),
        element_token=token,
    )


def pixel_target(x: int = 400, y: int = 300) -> PixelTarget:
    from friday.infrastructure.computer.models import PixelPoint

    return PixelTarget(ref=mail_ref(), point=PixelPoint(x=x, y=y))


def window_target() -> WindowTarget:
    return WindowTarget(ref=mail_ref())
