"""Immutable value objects for desktop computer use.

Deliberately infrastructure-owned, not application-owned: nothing in
friday.application ever sees these types. A computer-use tool's input and
output cross the ToolGateway boundary as JsonValue, exactly like every other
tool, so ExecuteToolAction stays unaware that a desktop exists.

Two invariants shape almost everything here:

* **Geometry is signed but bounded.** Secondary displays legitimately sit at
  negative origins, so coordinates may be negative; magnitudes are capped so
  a malformed driver reply cannot smuggle an absurd value into a click.
* **Observed UI text is untrusted input.** Any application can name a window
  or label a control whatever it likes, and that text reaches Claude's
  context. Titles, labels, and roles are therefore stripped of control
  characters, whitespace-collapsed, and length-bounded on the way in.

No Path, no file handles, no subprocess results, no MCP/JSON-RPC shapes.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from string import ascii_lowercase, digits

MAX_COORDINATE = 100_000
MAX_LABEL_CHARS = 256
MAX_ROLE_CHARS = 64
MAX_WINDOW_ID_CHARS = 256
DEFAULT_MAX_ELEMENTS = 500
MAX_ELEMENTS_CEILING = 5_000

_SNAPSHOT_ID_PATTERN = re.compile(r"^cs_[0-9a-f]{32}$")
_NON_ALPHANUMERIC_RUN = re.compile(r"[^a-z0-9]+")


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


def _ensure_identifier(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    if len(value) > MAX_WINDOW_ID_CHARS:
        raise ValueError(f"{field_name} must not exceed {MAX_WINDOW_ID_CHARS} characters")
    return value.strip()


def _ensure_aware_utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ScreenPoint:
    x: int
    y: int

    def __post_init__(self) -> None:
        _ensure_coordinate(self.x, field_name="ScreenPoint.x")
        _ensure_coordinate(self.y, field_name="ScreenPoint.y")


@dataclass(frozen=True, slots=True)
class ScreenBounds:
    """A half-open rectangle: ``x <= point.x < right`` and likewise for y."""

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

    @property
    def center(self) -> ScreenPoint:
        return ScreenPoint(x=self.x + self.width // 2, y=self.y + self.height // 2)

    def contains(self, point: ScreenPoint) -> bool:
        return self.x <= point.x < self.right and self.y <= point.y < self.bottom


@dataclass(frozen=True, slots=True)
class SnapshotId:
    """Identity of one capture, and therefore of one fence.

    Not a domain identifier: a snapshot is transient in-memory state, never a
    persisted entity, so it carries a legible ``cs_`` prefix instead of
    reusing the UUID-shaped domain identifier base.
    """

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not _SNAPSHOT_ID_PATTERN.match(self.value):
            raise ValueError(f"SnapshotId must match 'cs_<32 hex chars>', got {self.value!r}")

    def __str__(self) -> str:
        return self.value

    @classmethod
    def new(cls) -> SnapshotId:
        return cls(f"cs_{uuid.uuid4().hex}")


@dataclass(frozen=True, slots=True)
class WindowInfo:
    window_id: str
    title: str
    bounds: ScreenBounds
    application: str = ""
    is_active: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "window_id", _ensure_identifier(self.window_id, field_name="WindowInfo.window_id")
        )
        object.__setattr__(
            self, "title", _sanitize_observed_text(self.title, field_name="WindowInfo.title")
        )
        object.__setattr__(
            self,
            "application",
            _sanitize_observed_text(self.application, field_name="WindowInfo.application"),
        )


@dataclass(frozen=True, slots=True)
class CapturedElement:
    """One accessibility element Claude may address by ``element_id`` instead
    of by raw coordinates. Ids are only meaningful within their snapshot."""

    element_id: int
    role: str
    bounds: ScreenBounds
    label: str | None = None

    def __post_init__(self) -> None:
        if _ensure_int(self.element_id, field_name="CapturedElement.element_id") <= 0:
            raise ValueError("CapturedElement.element_id must be positive")
        object.__setattr__(self, "role", _normalize_role(self.role))
        if self.label is not None:
            object.__setattr__(
                self,
                "label",
                _sanitize_observed_text(self.label, field_name="CapturedElement.label"),
            )


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


@dataclass(frozen=True, slots=True)
class ScreenSnapshot:
    """What Friday observed at one instant, and the fence a later mutating
    action must bind to. Retention and staleness policy live with the
    registry that owns snapshots; this type only knows how to answer."""

    snapshot_id: SnapshotId
    captured_at: datetime
    window: WindowInfo
    elements: tuple[CapturedElement, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "captured_at",
            _ensure_aware_utc(self.captured_at, field_name="ScreenSnapshot.captured_at"),
        )
        identifiers = [element.element_id for element in self.elements]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("ScreenSnapshot.elements must not repeat an element_id")

    def element(self, element_id: int) -> CapturedElement | None:
        for element in self.elements:
            if element.element_id == element_id:
                return element
        return None

    def is_expired(self, now: datetime, *, ttl_seconds: float) -> bool:
        """True once the fence is too old to trust — and also whenever `now`
        precedes the capture, so clock skew fails closed rather than granting
        an unbounded lifetime."""
        age = (_ensure_aware_utc(now, field_name="now") - self.captured_at).total_seconds()
        return age < 0 or age >= ttl_seconds


class PointerButton(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class KeyName(StrEnum):
    """Named non-character keys Friday is willing to synthesize."""

    ENTER = "enter"
    TAB = "tab"
    ESCAPE = "escape"
    SPACE = "space"
    BACKSPACE = "backspace"
    DELETE = "delete"
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
class PointerTarget:
    """A resolved, already-fenced absolute point. ``window_id`` lets a driver
    dispatch input to a specific window in the background instead of warping
    the user's cursor."""

    point: ScreenPoint
    window_id: str | None = None

    def __post_init__(self) -> None:
        if self.window_id is not None:
            object.__setattr__(
                self,
                "window_id",
                _ensure_identifier(self.window_id, field_name="PointerTarget.window_id"),
            )


@dataclass(frozen=True, slots=True)
class ScrollDelta:
    dx: int
    dy: int

    def __post_init__(self) -> None:
        _ensure_coordinate(self.dx, field_name="ScrollDelta.dx")
        _ensure_coordinate(self.dy, field_name="ScrollDelta.dy")
        if self.dx == 0 and self.dy == 0:
            raise ValueError("ScrollDelta must move along at least one axis")


@dataclass(frozen=True, slots=True)
class CaptureRequest:
    window_id: str | None = None
    include_screenshot: bool = True
    include_elements: bool = True
    max_elements: int = DEFAULT_MAX_ELEMENTS

    def __post_init__(self) -> None:
        if self.window_id is not None:
            object.__setattr__(
                self,
                "window_id",
                _ensure_identifier(self.window_id, field_name="CaptureRequest.window_id"),
            )
        budget = _ensure_int(self.max_elements, field_name="CaptureRequest.max_elements")
        if budget <= 0 or budget > MAX_ELEMENTS_CEILING:
            raise ValueError(
                f"CaptureRequest.max_elements must be between 1 and {MAX_ELEMENTS_CEILING}"
            )


@dataclass(frozen=True, slots=True)
class Screenshot:
    """Raw image bytes on their way to artifact storage.

    Bytes stop at the gateway: it writes the file and hands the application
    layer an ArtifactCandidate location, never a base64 payload.
    """

    data: bytes
    media_type: str
    width: int
    height: int

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes) or not self.data:
            raise ValueError("Screenshot.data must be non-empty bytes")
        if not isinstance(self.media_type, str) or not self.media_type.strip():
            raise ValueError("Screenshot.media_type must be a non-empty string")
        for name, extent in (("width", self.width), ("height", self.height)):
            if _ensure_int(extent, field_name=f"Screenshot.{name}") <= 0:
                raise ValueError(f"Screenshot.{name} must be positive")


@dataclass(frozen=True, slots=True)
class CaptureResult:
    window: WindowInfo
    elements: tuple[CapturedElement, ...] = ()
    screenshot: Screenshot | None = None


@dataclass(frozen=True, slots=True)
class DriverResult:
    """What a mutating driver call observed afterwards. Drivers signal failure
    by raising ComputerDriverError, so there is no ok/status flag here."""

    pointer_position: ScreenPoint | None = field(default=None)
    window_id: str | None = field(default=None)
