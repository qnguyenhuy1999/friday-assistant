"""Phase 13 computer-use settings: desktop capability limits and the driver command.

Separate from WorkerSettings, RuntimeSettings, and MemorySettings — one module
per concern, as with Phase 12's memory settings.

**Computer use defaults off.** Not because it is unfinished, but because it is
the only capability in Friday that can act on the human's own desktop session,
outside any workspace confinement. Enabling it is a deliberate operator
decision, and every limit here has a default that stays useful if the operator
sets nothing else.

The two ceilings worth understanding:

* ``max_scroll_amount`` is the *operational* scroll bound, in wheel notches or
  keystroke repetitions — the units the driver actually scrolls in. The driver's
  own default is 3; the ceiling keeps a scroll a scroll rather than a fling to
  the end of an unbounded feed.
* ``max_capture_bytes`` bounds a screenshot before its bytes are decoded, let
  alone written. A 6K display screenshots to a few megabytes; the default
  leaves room for that and refuses anything that looks like a different
  problem.

There is no capture TTL or snapshot budget any more. A capture is no longer a
stored fence with a lifetime: every mutating tool re-captures its window
immediately before acting and revalidates the approved target against what it
just saw, so there is nothing retained whose staleness an operator could tune.

The workspace root is deliberately absent: it belongs to RuntimeSettings, and
reading it from the environment a second time here would let the directory
screenshots are written to drift away from the one workspace tools are confined
to. The composition root passes the single value through.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from math import isfinite

_DEFAULT_COMPUTER_USE_ENABLED = False
_DEFAULT_CUA_DRIVER_CMD = "cua-driver mcp"
_DEFAULT_COMPUTER_TIMEOUT_SECONDS = 15.0
_DEFAULT_COMPUTER_MAX_CAPTURE_BYTES = 8_000_000
_DEFAULT_COMPUTER_MAX_TYPE_CHARS = 4_096
_DEFAULT_COMPUTER_MAX_SCROLL_AMOUNT = 10
_DEFAULT_COMPUTER_MAX_ELEMENTS = 500
_DEFAULT_CUA_TELEMETRY_ENABLED = False

_MAX_SCROLL_AMOUNT_CEILING = 50


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
    max_scroll_amount: int
    max_elements: int
    telemetry_enabled: bool

    def __post_init__(self) -> None:
        positives: dict[str, float] = {
            "timeout_seconds": self.timeout_seconds,
            "max_capture_bytes": self.max_capture_bytes,
            "max_type_chars": self.max_type_chars,
            "max_scroll_amount": self.max_scroll_amount,
            "max_elements": self.max_elements,
        }
        for name, value in positives.items():
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if self.max_scroll_amount > _MAX_SCROLL_AMOUNT_CEILING:
            raise ValueError(
                f"max_scroll_amount must not exceed {_MAX_SCROLL_AMOUNT_CEILING} notches"
            )
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
            max_scroll_amount=int(
                os.environ.get(
                    "FRIDAY_COMPUTER_MAX_SCROLL_AMOUNT", _DEFAULT_COMPUTER_MAX_SCROLL_AMOUNT
                )
            ),
            max_elements=int(
                os.environ.get("FRIDAY_COMPUTER_MAX_ELEMENTS", _DEFAULT_COMPUTER_MAX_ELEMENTS)
            ),
            telemetry_enabled=_parse_bool(
                "FRIDAY_CUA_TELEMETRY_ENABLED", _DEFAULT_CUA_TELEMETRY_ENABLED
            ),
        )
