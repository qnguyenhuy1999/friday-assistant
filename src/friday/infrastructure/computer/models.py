"""Immutable value objects for desktop computer use.

Deliberately infrastructure-owned, not application-owned: nothing in
friday.application ever sees these types. A computer-use tool's input and
output cross the ToolGateway boundary as JsonValue, exactly like every other
tool, so ExecuteToolAction stays unaware that a desktop exists.

Three invariants shape almost everything here:

* **Geometry is signed but bounded.** Secondary displays legitimately sit at
  negative origins, so desktop coordinates may be negative; magnitudes are
  capped so a malformed driver reply cannot smuggle an absurd value into a
  click.
* **Observed UI text is untrusted input.** Any application can name a window
  or label a control whatever it likes, and that text reaches Claude's
  context. Titles, labels, and roles are therefore stripped of control
  characters, whitespace-collapsed, and length-bounded on the way in.
* **Two coordinate spaces never mix.** A window's `bounds` are desktop points
  (where the window sits on the desktop); an element's `frame` is in
  *window-local screenshot pixels* (where something sits inside the image
  Friday was handed). Different origin, and on a Retina
  display a different scale. They are separate types here so that "is this
  point inside that window?" cannot be asked of two values that were never in
  the same space — that mistake produces a click which lands somewhere
  plausible and wrong.

Window identity is `(pid, window_id)` and both halves are integers, because
that is what the driver actually uses: `window_id` alone is not addressable,
and an element index is only meaningful for the window it came from.

No Path, no file handles, no subprocess results, no MCP/JSON-RPC shapes.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from string import ascii_lowercase, digits

MAX_COORDINATE = 100_000
MAX_LABEL_CHARS = 256
MAX_ROLE_CHARS = 64
MAX_TOKEN_CHARS = 256
DEFAULT_MAX_ELEMENTS = 500
MAX_ELEMENTS_CEILING = 5_000

MAX_PID = 4_194_304
"""Above any real pid on the platforms Friday runs on (Linux's pid_max ceiling;
macOS is far lower). Not an assertion about the OS — a bound so a malformed
reply cannot carry an arbitrary integer into a targeting decision."""

MAX_WINDOW_ID = 2**31 - 1
"""CGWindowID is a 32-bit unsigned value in practice; this bounds it to the
signed range every JSON consumer can represent losslessly."""

MIN_CLICK_COUNT = 1
MAX_CLICK_COUNT = 2
"""A click is a click or a double-click. Anything beyond that is a distinct
interaction Claude should have to propose — and get approved — separately."""

DEFAULT_SCROLL_AMOUNT = 3
MAX_SCROLL_AMOUNT = 50
"""Wheel notches or keystroke repetitions. The ceiling keeps a scroll a scroll
rather than a fling to the end of an unbounded feed; the driver's own default
is 3, which is also Friday's."""

ALLOWED_SCREENSHOT_MEDIA_TYPES = frozenset({"image/png"})
"""Closed set, not a hint from the driver. Phase 13 persists PNG only, so a
driver claiming `image/svg+xml` (scriptable) or `text/html` cannot talk Friday
into writing it into the artifact store under an image kind."""

MAX_SCREENSHOT_DIMENSION = 20_000
MAX_SCREENSHOT_BYTES_CEILING = 64_000_000
"""Hard model-level ceilings. The configured FRIDAY_COMPUTER_MAX_CAPTURE_BYTES
is expected to be far smaller; these only stop an absurd value from ever being
represented, so nothing downstream has to defend against one."""

_CAPTURE_ID_PATTERN = re.compile(r"^cs_[0-9a-f]{32}$")
_NON_ALPHANUMERIC_RUN = re.compile(r"[^a-z0-9]+")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._:-]+$")


def _sanitize_observed_text(
    value: str, *, field_name: str, max_chars: int = MAX_LABEL_CHARS
) -> str:
    """Neutralize one piece of attacker-influenceable UI text.

    Unicode control/format characters (category ``C``) become spaces, runs of
    whitespace collapse to a single space, and the result is truncated. A
    blank result is allowed — plenty of real windows have no title.
    """
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    decontrolled = "".join(
        " " if unicodedata.category(char).startswith("C") else char for char in value
    )
    return " ".join(decontrolled.split())[:max_chars]


def _ensure_int(value: object, *, field_name: str) -> int:
    """Reject bools, which are ints in Python but never valid geometry."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _ensure_coordinate(value: object, *, field_name: str) -> int:
    coordinate = _ensure_int(value, field_name=field_name)
    if abs(coordinate) > MAX_COORDINATE:
        raise ValueError(f"{field_name} magnitude must not exceed {MAX_COORDINATE}")
    return coordinate


def _ensure_pixel(value: object, *, field_name: str) -> int:
    """A screenshot pixel is an offset into an image, so it is never negative."""
    pixel = _ensure_int(value, field_name=field_name)
    if pixel < 0:
        raise ValueError(f"{field_name} must not be negative")
    if pixel > MAX_COORDINATE:
        raise ValueError(f"{field_name} must not exceed {MAX_COORDINATE}")
    return pixel


def _ensure_bounded_id(value: object, *, field_name: str, maximum: int) -> int:
    identifier = _ensure_int(value, field_name=field_name)
    if identifier <= 0:
        raise ValueError(f"{field_name} must be positive")
    if identifier > maximum:
        raise ValueError(f"{field_name} must not exceed {maximum}")
    return identifier


def _ensure_aware_utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


# --- desktop-point geometry ------------------------------------------------
#
# Where a window sits on the desktop. Used for reporting window geometry and
# the OS cursor position. Never used to address a click.


@dataclass(frozen=True, slots=True)
class ScreenPoint:
    x: int
    y: int

    def __post_init__(self) -> None:
        _ensure_coordinate(self.x, field_name="ScreenPoint.x")
        _ensure_coordinate(self.y, field_name="ScreenPoint.y")


@dataclass(frozen=True, slots=True)
class ScreenBounds:
    """A half-open rectangle in desktop points."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        _ensure_coordinate(self.x, field_name="ScreenBounds.x")
        _ensure_coordinate(self.y, field_name="ScreenBounds.y")
        for name, extent in (("width", self.width), ("height", self.height)):
            if _ensure_int(extent, field_name=f"ScreenBounds.{name}") <= 0:
                raise ValueError(f"ScreenBounds.{name} must be positive")
            if extent > MAX_COORDINATE:
                raise ValueError(f"ScreenBounds.{name} must not exceed {MAX_COORDINATE}")

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height


# --- window-local pixel geometry ------------------------------------------
#
# Where something sits inside the screenshot Friday was handed. This is the
# only space an action may be addressed in.


@dataclass(frozen=True, slots=True)
class PixelPoint:
    """A point in window-local screenshot pixels, top-left origin."""

    x: int
    y: int

    def __post_init__(self) -> None:
        _ensure_pixel(self.x, field_name="PixelPoint.x")
        _ensure_pixel(self.y, field_name="PixelPoint.y")


@dataclass(frozen=True, slots=True)
class PixelFrame:
    """An element's rectangle in window-local screenshot pixels."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        _ensure_pixel(self.x, field_name="PixelFrame.x")
        _ensure_pixel(self.y, field_name="PixelFrame.y")
        for name, extent in (("width", self.width), ("height", self.height)):
            if _ensure_int(extent, field_name=f"PixelFrame.{name}") <= 0:
                raise ValueError(f"PixelFrame.{name} must be positive")
            if extent > MAX_COORDINATE:
                raise ValueError(f"PixelFrame.{name} must not exceed {MAX_COORDINATE}")

    @property
    def center(self) -> PixelPoint:
        return PixelPoint(x=self.x + self.width // 2, y=self.y + self.height // 2)


@dataclass(frozen=True, slots=True)
class ScreenshotExtent:
    """The pixel dimensions of one capture — the authority on which coordinates
    are addressable.

    The window's own `bounds` cannot answer that question: bounds are desktop
    points, and a Retina capture is twice their size. Only the image knows how
    big the image is.
    """

    width: int
    height: int

    def __post_init__(self) -> None:
        for name, extent in (("width", self.width), ("height", self.height)):
            if _ensure_int(extent, field_name=f"ScreenshotExtent.{name}") <= 0:
                raise ValueError(f"ScreenshotExtent.{name} must be positive")
            if extent > MAX_SCREENSHOT_DIMENSION:
                raise ValueError(
                    f"ScreenshotExtent.{name} must not exceed {MAX_SCREENSHOT_DIMENSION}"
                )

    def contains(self, point: PixelPoint) -> bool:
        return 0 <= point.x < self.width and 0 <= point.y < self.height


# --- identity -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CaptureId:
    """Correlation id for one capture.

    Explicitly **not** a fence. It names the artifact file and lets a log line
    be tied to the image it describes; nothing authorizes an action by citing
    it, and no mutating tool accepts one. Carries a legible ``cs_`` prefix
    rather than reusing the UUID-shaped domain identifier base, because a
    capture is transient in-memory state and never a persisted entity.
    """

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not _CAPTURE_ID_PATTERN.match(self.value):
            raise ValueError(f"CaptureId must match 'cs_<32 hex chars>', got {self.value!r}")

    def __str__(self) -> str:
        return self.value

    @classmethod
    def new(cls) -> CaptureId:
        return cls(f"cs_{uuid.uuid4().hex}")


@dataclass(frozen=True, slots=True)
class WindowRef:
    """Backend window identity: a process and one of its windows.

    Both halves are required for every addressed action. `window_id` alone is
    not enough — the driver resolves element indices per `(pid, window_id)` —
    and a pid alone names an application, not a surface.
    """

    pid: int
    window_id: int

    def __post_init__(self) -> None:
        _ensure_bounded_id(self.pid, field_name="WindowRef.pid", maximum=MAX_PID)
        _ensure_bounded_id(self.window_id, field_name="WindowRef.window_id", maximum=MAX_WINDOW_ID)


@dataclass(frozen=True, slots=True)
class WindowInfo:
    """One observed window.

    `z_index`, `is_on_screen`, and `on_current_space` are reported rather than
    folded into an `is_active` flag: "frontmost" is derivable from them, but
    z-order is not keyboard-focus identity, and a single boolean would claim it
    was.
    """

    ref: WindowRef
    title: str
    bounds: ScreenBounds
    app_name: str = ""
    z_index: int = 0
    is_on_screen: bool = False
    on_current_space: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "title", _sanitize_observed_text(self.title, field_name="WindowInfo.title")
        )
        object.__setattr__(
            self,
            "app_name",
            _sanitize_observed_text(self.app_name, field_name="WindowInfo.app_name"),
        )
        _ensure_coordinate(self.z_index, field_name="WindowInfo.z_index")
        for name in ("is_on_screen", "on_current_space"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"WindowInfo.{name} must be a boolean")

    @property
    def pid(self) -> int:
        return self.ref.pid

    @property
    def window_id(self) -> int:
        return self.ref.window_id


# --- observed elements ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapturedElement:
    """One accessibility element from a single capture.

    `element_index` and `element_token` are both per-capture handles: the
    driver replaces its index map on every snapshot of the same window, so
    neither survives a re-capture and neither is something Claude may cache.
    What Claude addresses instead is (`role`, `label`) — see ElementDescriptor.
    """

    element_index: int
    role: str
    frame: PixelFrame
    label: str | None = None
    element_token: str | None = None

    def __post_init__(self) -> None:
        if _ensure_int(self.element_index, field_name="CapturedElement.element_index") < 0:
            raise ValueError("CapturedElement.element_index must not be negative")
        object.__setattr__(self, "role", _normalize_role(self.role))
        if self.label is not None:
            object.__setattr__(
                self,
                "label",
                _sanitize_observed_text(self.label, field_name="CapturedElement.label"),
            )
        if self.element_token is not None:
            object.__setattr__(self, "element_token", _normalize_token(self.element_token))

    @property
    def descriptor(self) -> ElementDescriptor:
        return ElementDescriptor(role=self.role, label=self.label or "")


def _normalize_role(role: str) -> str:
    """Fold an accessibility role from any platform into lowercase snake case.

    Roles are free-form across AX/UIAutomation/AT-SPI, so this normalizes
    rather than validating against a closed enum that real platforms would
    immediately violate.
    """
    sanitized = _sanitize_observed_text(role, field_name="CapturedElement.role").lower()
    normalized = _NON_ALPHANUMERIC_RUN.sub("_", sanitized).strip("_")[:MAX_ROLE_CHARS]
    if not normalized:
        raise ValueError("CapturedElement.role must not be empty")
    return normalized


def _normalize_token(token: str) -> str:
    """An opaque driver handle, bounded and charset-restricted.

    Opaque does not mean unvalidated: it is echoed back to the driver, so it is
    held to a conservative charset rather than passed through as arbitrary
    observed text.
    """
    if not isinstance(token, str):
        raise ValueError("CapturedElement.element_token must be a string")
    stripped = token.strip()
    if not stripped or len(stripped) > MAX_TOKEN_CHARS:
        raise ValueError(f"CapturedElement.element_token must be 1..{MAX_TOKEN_CHARS} characters")
    if not _SAFE_TOKEN.match(stripped):
        raise ValueError("CapturedElement.element_token contains unsupported characters")
    return stripped


@dataclass(frozen=True, slots=True)
class ElementDescriptor:
    """The stable, human-meaningful handle for one control: its role and label.

    This is what an approval binds to. An index would not survive the
    re-capture that every mutating action performs, and a human approving
    "click element 14" has been told nothing about what they approved.

    Matching is exact on the normalized values, and a descriptor that matches
    more than one element in its window is refused rather than resolved by
    picking one.
    """

    role: str
    label: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _normalize_role(self.role))
        object.__setattr__(
            self, "label", _sanitize_observed_text(self.label, field_name="ElementDescriptor.label")
        )

    def matches(self, element: CapturedElement) -> bool:
        return element.role == self.role and (element.label or "") == self.label

    def __str__(self) -> str:
        return f"{self.role} {self.label!r}" if self.label else self.role


# --- captures -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Screenshot:
    """Raw image bytes on their way to artifact storage.

    Bytes stop at the gateway: it writes the file and hands the application
    layer an ArtifactCandidate location, never a base64 payload.

    Bounded at construction on every axis a driver could inflate — byte count,
    dimensions, and media type — because this type is the last place the bytes
    are anonymous. Past here they are a file path and a checksum.
    """

    data: bytes
    media_type: str
    width: int
    height: int

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes) or not self.data:
            raise ValueError("Screenshot.data must be non-empty bytes")
        if len(self.data) > MAX_SCREENSHOT_BYTES_CEILING:
            raise ValueError(
                f"Screenshot.data must not exceed {MAX_SCREENSHOT_BYTES_CEILING} bytes"
            )
        if self.media_type not in ALLOWED_SCREENSHOT_MEDIA_TYPES:
            raise ValueError(
                "Screenshot.media_type must be one of "
                f"{sorted(ALLOWED_SCREENSHOT_MEDIA_TYPES)}, got {self.media_type!r}"
            )
        for name, extent in (("width", self.width), ("height", self.height)):
            if _ensure_int(extent, field_name=f"Screenshot.{name}") <= 0:
                raise ValueError(f"Screenshot.{name} must be positive")
            if extent > MAX_SCREENSHOT_DIMENSION:
                raise ValueError(
                    f"Screenshot.{name} must not exceed {MAX_SCREENSHOT_DIMENSION} pixels"
                )

    @property
    def extent(self) -> ScreenshotExtent:
        return ScreenshotExtent(width=self.width, height=self.height)


@dataclass(frozen=True, slots=True)
class CaptureRequest:
    """Ask the driver to walk one window. Both identity halves are required —
    there is no "capture whatever is in front" mode, because an action fenced
    against an unnamed window is an action nobody can review."""

    ref: WindowRef
    include_screenshot: bool = True
    max_elements: int = DEFAULT_MAX_ELEMENTS

    def __post_init__(self) -> None:
        if not isinstance(self.include_screenshot, bool):
            raise ValueError("CaptureRequest.include_screenshot must be a boolean")
        budget = _ensure_int(self.max_elements, field_name="CaptureRequest.max_elements")
        if budget <= 0 or budget > MAX_ELEMENTS_CEILING:
            raise ValueError(
                f"CaptureRequest.max_elements must be between 1 and {MAX_ELEMENTS_CEILING}"
            )


@dataclass(frozen=True, slots=True)
class CaptureResult:
    """What one window-state call observed: the controls, and optionally an image.

    No window record. Window metadata — title, bounds, stacking — comes from the
    window listing, not from walking a window's contents, and inventing a
    combined reply here would have obliged the adapter to make a second call it
    then had to pretend was part of one observation.
    """

    elements: tuple[CapturedElement, ...] = ()
    screenshot: Screenshot | None = None


@dataclass(frozen=True, slots=True)
class ScreenSnapshot:
    """One capture, as Friday holds it for the length of a single tool call.

    No TTL and no expiry check, because nothing outlives its call: a mutating
    action captures, resolves its target against what it just captured, and
    acts. There is no stored snapshot for a later action to cite, so there is
    no staleness to police — the freshness guarantee comes from having just
    looked, not from a clock.
    """

    capture_id: CaptureId
    captured_at: datetime
    window: WindowInfo
    elements: tuple[CapturedElement, ...] = ()
    extent: ScreenshotExtent | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "captured_at",
            _ensure_aware_utc(self.captured_at, field_name="ScreenSnapshot.captured_at"),
        )
        indices = [element.element_index for element in self.elements]
        if len(indices) != len(set(indices)):
            raise ValueError("ScreenSnapshot.elements must not repeat an element_index")

    def matching(self, descriptor: ElementDescriptor) -> tuple[CapturedElement, ...]:
        return tuple(element for element in self.elements if descriptor.matches(element))


# --- input vocabulary -----------------------------------------------------


class PointerButton(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class ScrollDirection(StrEnum):
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


class ScrollGranularity(StrEnum):
    LINE = "line"
    PAGE = "page"


class KeyName(StrEnum):
    """Named non-character keys Friday is willing to synthesize."""

    ENTER = "enter"
    TAB = "tab"
    ESCAPE = "escape"
    SPACE = "space"
    BACKSPACE = "backspace"
    ARROW_UP = "arrow_up"
    ARROW_DOWN = "arrow_down"
    ARROW_LEFT = "arrow_left"
    ARROW_RIGHT = "arrow_right"
    HOME = "home"
    END = "end"
    PAGE_UP = "page_up"
    PAGE_DOWN = "page_down"


class KeyModifier(StrEnum):
    META = "meta"
    CTRL = "ctrl"
    ALT = "alt"
    SHIFT = "shift"


CHARACTER_KEYS = frozenset(ascii_lowercase + digits)
ALLOWED_KEYS = frozenset(key.value for key in KeyName) | CHARACTER_KEYS
_MODIFIER_ORDER = (KeyModifier.META, KeyModifier.CTRL, KeyModifier.ALT, KeyModifier.SHIFT)


@dataclass(frozen=True, slots=True)
class Keystroke:
    """One key press, optionally with modifiers.

    ``key`` is either a KeyName value or a single ``[a-z0-9]`` character —
    an allowlist, never arbitrary driver passthrough. Modifiers are
    deduplicated and canonically ordered so that two spellings of the same
    combination compare equal, which is what lets a deny-list be checked by
    value rather than by string matching.
    """

    key: str
    modifiers: tuple[KeyModifier, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.key, str):
            raise ValueError("Keystroke.key must be a string")
        normalized = self.key.strip().lower()
        if normalized not in ALLOWED_KEYS:
            raise ValueError(f"Keystroke.key is not an allowed key: {self.key!r}")
        object.__setattr__(self, "key", normalized)
        unknown = [modifier for modifier in self.modifiers if not isinstance(modifier, KeyModifier)]
        if unknown:
            raise ValueError(f"Keystroke.modifiers contains unknown modifier(s): {unknown!r}")
        requested = set(self.modifiers)
        object.__setattr__(self, "modifiers", tuple(m for m in _MODIFIER_ORDER if m in requested))

    @property
    def combination(self) -> str:
        """Canonical human-readable form, e.g. ``meta+alt+escape``."""
        return "+".join([*(modifier.value for modifier in self.modifiers), self.key])


@dataclass(frozen=True, slots=True)
class ScrollCommand:
    """A scroll, expressed the way the driver expresses one: a direction plus a
    magnitude in notches or keystroke repetitions, not a pixel delta."""

    direction: ScrollDirection
    amount: int = DEFAULT_SCROLL_AMOUNT
    by: ScrollGranularity = ScrollGranularity.LINE

    def __post_init__(self) -> None:
        if not isinstance(self.direction, ScrollDirection):
            raise ValueError("ScrollCommand.direction must be a ScrollDirection")
        if not isinstance(self.by, ScrollGranularity):
            raise ValueError("ScrollCommand.by must be a ScrollGranularity")
        amount = _ensure_int(self.amount, field_name="ScrollCommand.amount")
        if amount < 1 or amount > MAX_SCROLL_AMOUNT:
            raise ValueError(f"ScrollCommand.amount must be between 1 and {MAX_SCROLL_AMOUNT}")


# --- resolved targets -----------------------------------------------------
#
# What reaches the driver. Both forms are already fenced: the window was
# observed in this same tool call, and the element or pixel was found in that
# observation. A driver never resolves a target itself.


@dataclass(frozen=True, slots=True)
class ElementTarget:
    """An accessibility-addressed target: the driver's preferred path.

    Works on backgrounded windows, steals no focus, and carries the role and
    label that were matched so the durable result records *what* was acted on
    rather than only where.
    """

    ref: WindowRef
    element_index: int
    descriptor: ElementDescriptor
    element_token: str | None = None

    def __post_init__(self) -> None:
        if _ensure_int(self.element_index, field_name="ElementTarget.element_index") < 0:
            raise ValueError("ElementTarget.element_index must not be negative")
        if self.element_token is not None:
            object.__setattr__(self, "element_token", _normalize_token(self.element_token))


@dataclass(frozen=True, slots=True)
class PixelTarget:
    """A pixel-addressed target, in window-local screenshot coordinates.

    The escape hatch for canvas, video, WebGL, and other custom-drawn surfaces
    that never appear in an accessibility tree. Proven to lie inside the
    capture's own extent before it is built.
    """

    ref: WindowRef
    point: PixelPoint


@dataclass(frozen=True, slots=True)
class WindowTarget:
    """A whole-window target, for input that genuinely has no element.

    `escape`, `cmd+c`, and their kin are directed at a window, not at a
    control. Requiring a spurious element for them would push Claude into
    naming an arbitrary one just to satisfy the schema — a worse fence than
    admitting the action is window-level.
    """

    ref: WindowRef


AddressedTarget = ElementTarget | PixelTarget
"""Targets that name a specific place inside the window: what a click, a
scroll, or a text insert must have."""

ActionTarget = ElementTarget | PixelTarget | WindowTarget


@dataclass(frozen=True, slots=True)
class DriverResult:
    """What a mutating driver call reported afterwards.

    Drivers signal failure by raising, so there is no ok/status flag. `effect`
    and `verified` carry the driver's own honesty about whether it could
    confirm the action landed: a click is never verifiable by read-back, a text
    insert sometimes is. Passing that through unchanged is what lets Claude
    know it must confirm with a fresh capture instead of assuming success.
    """

    effect: str | None = None
    verified: bool | None = None

    def __post_init__(self) -> None:
        if self.effect is not None:
            object.__setattr__(
                self,
                "effect",
                _sanitize_observed_text(
                    self.effect, field_name="DriverResult.effect", max_chars=64
                ),
            )
        if self.verified is not None and not isinstance(self.verified, bool):
            raise ValueError("DriverResult.verified must be a boolean or None")
