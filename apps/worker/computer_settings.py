"""Phase 13 computer-use settings: desktop capability limits and the driver command.

Separate from WorkerSettings, RuntimeSettings, and MemorySettings — one module
per concern, as with Phase 12's memory settings.

**Computer use defaults off.** Not because it is unfinished, but because it is
the only capability in Friday that can act on the human's own desktop session,
outside any workspace confinement. Enabling it is a deliberate operator
decision, and every limit here has a default that stays useful if the operator
sets nothing else.

The two ceilings worth understanding:

* ``max_scroll_delta`` is the *operational* scroll bound. The value objects
  allow ±100000 because that is the representable range for screen geometry;
  a scroll of 100000 is a fling to the end of an infinite feed, so the policy
  ceiling is three orders of magnitude smaller.
* ``max_capture_bytes`` bounds a screenshot before its bytes are decoded, let
  alone written. A 6K display screenshots to a few megabytes; the default
  leaves room for that and refuses anything that looks like a different
  problem.

The workspace root is deliberately absent: it belongs to RuntimeSettings, and
reading it from the environment a second time here would let the directory
screenshots are written to drift away from the one workspace tools are confined
to. The composition root passes the single value through.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass

_DEFAULT_COMPUTER_USE_ENABLED = False
_DEFAULT_CUA_DRIVER_CMD = "cua-driver"
_DEFAULT_COMPUTER_TIMEOUT_SECONDS = 15.0
_DEFAULT_COMPUTER_MAX_CAPTURE_BYTES = 8_000_000
_DEFAULT_COMPUTER_MAX_TYPE_CHARS = 4_096
_DEFAULT_COMPUTER_MAX_SCROLL_DELTA = 5_000
_DEFAULT_COMPUTER_CAPTURE_TTL_SECONDS = 10.0
_DEFAULT_COMPUTER_MAX_SNAPSHOTS = 32
_DEFAULT_COMPUTER_MAX_ELEMENTS = 500
_DEFAULT_CUA_TELEMETRY_ENABLED = False

_MAX_CAPTURE_TTL_SECONDS = 300.0
_MAX_SCROLL_DELTA_CEILING = 100_000


def _parse_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _parse_command(value: str) -> tuple[str, ...]:
    """Parse the driver command with shell-like quoting but no shell.

    `shlex.split` handles `"/opt/my tools/cua-driver" --serve` correctly; the
    result is an argv list handed to `subprocess` without `shell=True`, so
    nothing in this string is ever interpreted by a shell.
    """
    try:
        parts = shlex.split(value)
    except ValueError as error:
        raise ValueError("FRIDAY_CUA_DRIVER_CMD is not a valid command line") from error
    return tuple(parts)


@dataclass(frozen=True, slots=True)
class ComputerSettings:
    computer_use_enabled: bool
    driver_command: tuple[str, ...]
    timeout_seconds: float
    max_capture_bytes: int
    max_type_chars: int
    max_scroll_delta: int
    capture_ttl_seconds: float
    max_snapshots: int
    max_elements: int
    telemetry_enabled: bool

    def __post_init__(self) -> None:
        positives: dict[str, float] = {
            "timeout_seconds": self.timeout_seconds,
            "max_capture_bytes": self.max_capture_bytes,
            "max_type_chars": self.max_type_chars,
            "max_scroll_delta": self.max_scroll_delta,
            "capture_ttl_seconds": self.capture_ttl_seconds,
            "max_snapshots": self.max_snapshots,
            "max_elements": self.max_elements,
        }
        for name, value in positives.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.capture_ttl_seconds > _MAX_CAPTURE_TTL_SECONDS:
            raise ValueError(
                "capture_ttl_seconds must not exceed "
                f"{_MAX_CAPTURE_TTL_SECONDS:.0f}s — a stale capture is not a fence"
            )
        if self.max_scroll_delta > _MAX_SCROLL_DELTA_CEILING:
            raise ValueError("max_scroll_delta exceeds the representable scroll range")
        if not self.computer_use_enabled:
            return
        if not self.driver_command:
            raise ValueError("driver_command must not be empty when computer use is enabled")

    @classmethod
    def from_env(cls) -> ComputerSettings:
        return cls(
            computer_use_enabled=_parse_bool(
                "FRIDAY_COMPUTER_USE_ENABLED", _DEFAULT_COMPUTER_USE_ENABLED
            ),
            driver_command=_parse_command(
                os.environ.get("FRIDAY_CUA_DRIVER_CMD", _DEFAULT_CUA_DRIVER_CMD)
            ),
            timeout_seconds=float(
                os.environ.get("FRIDAY_COMPUTER_TIMEOUT_SECONDS", _DEFAULT_COMPUTER_TIMEOUT_SECONDS)
            ),
            max_capture_bytes=int(
                os.environ.get(
                    "FRIDAY_COMPUTER_MAX_CAPTURE_BYTES", _DEFAULT_COMPUTER_MAX_CAPTURE_BYTES
                )
            ),
            max_type_chars=int(
                os.environ.get("FRIDAY_COMPUTER_MAX_TYPE_CHARS", _DEFAULT_COMPUTER_MAX_TYPE_CHARS)
            ),
            max_scroll_delta=int(
                os.environ.get(
                    "FRIDAY_COMPUTER_MAX_SCROLL_DELTA", _DEFAULT_COMPUTER_MAX_SCROLL_DELTA
                )
            ),
            capture_ttl_seconds=float(
                os.environ.get(
                    "FRIDAY_COMPUTER_CAPTURE_TTL_SECONDS", _DEFAULT_COMPUTER_CAPTURE_TTL_SECONDS
                )
            ),
            max_snapshots=int(
                os.environ.get("FRIDAY_COMPUTER_MAX_SNAPSHOTS", _DEFAULT_COMPUTER_MAX_SNAPSHOTS)
            ),
            max_elements=int(
                os.environ.get("FRIDAY_COMPUTER_MAX_ELEMENTS", _DEFAULT_COMPUTER_MAX_ELEMENTS)
            ),
            telemetry_enabled=_parse_bool(
                "FRIDAY_CUA_TELEMETRY_ENABLED", _DEFAULT_CUA_TELEMETRY_ENABLED
            ),
        )
