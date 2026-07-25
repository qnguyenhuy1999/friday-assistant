"""Keyboard primitives: `computer.type_text`, `computer.press_key`, `computer.hotkey`.

The keyboard is the widest computer-use surface, because a key sequence can
reach things no click can: a shell, a login prompt, a system dialog. So the
allowlists here are closed on every axis at once.

* **Text** is bounded, control-free, and screened for credential shapes. The
  screen is defence in depth — Friday's real position is that it never puts a
  credential where Claude could propose typing it, and there is no
  secret-retrieval path in the system to reach one.
* **Keys** come from `ALLOWED_KEYS` — the named set plus single `[a-z0-9]`
  characters. No raw keycodes, ever: a keycode is an unreviewable integer that
  means whatever the current layout says it means.
* **Hotkeys** are compared as normalized `Keystroke` values against a
  deny-list. Normalization first is what makes the comparison sound —
  `["shift","meta"]` and `["meta","shift"]` are the same combination, and
  substring matching on a rendered string would miss one spelling or refuse an
  innocent superset.

The deny-list covers session-level destruction: force quit, log out, lock,
shut down, restart. It is not a claim of completeness — a platform can always
bind something dangerous somewhere — which is why every hotkey needs approval
regardless of whether the deny-list recognizes it.
"""

from __future__ import annotations

from dataclasses import dataclass

from friday.application.errors import ToolInputInvalid
from friday.application.secret_shapes import contains_secret_shape
from friday.application.tool_gateway import ToolExecutionResult
from friday.domain.json_value import JsonValue
from friday.infrastructure.computer.context import ComputerToolContext
from friday.infrastructure.computer.driver import ComputerDriver
from friday.infrastructure.computer.errors import HotkeyRejected, TextRejected
from friday.infrastructure.computer.models import (
    ALLOWED_KEYS,
    KeyModifier,
    KeyName,
    Keystroke,
)
from friday.infrastructure.computer.snapshots import SnapshotRegistry
from friday.infrastructure.computer.targets import FENCE_FIELDS, resolve_fence
from friday.infrastructure.computer.tool_input import (
    parse_object,
    required_field,
    required_str,
    string_list,
)

DEFAULT_MAX_TYPE_CHARS = 4_096
MAX_MODIFIERS = len(KeyModifier)
MAX_KEY_CHARS = 32

_TYPE_TEXT_FIELDS = FENCE_FIELDS | {"text"}
_PRESS_KEY_FIELDS = FENCE_FIELDS | {"key"}
_HOTKEY_FIELDS = FENCE_FIELDS | {"key", "modifiers"}

_ALLOWED_TEXT_CONTROLS = frozenset({"\t", "\n"})
"""Tab and newline are legitimate in typed prose — a form field, a message
body. Every other control character is either meaningless to type or a way to
smuggle terminal behaviour into what looks like plain text."""

DENIED_HOTKEYS: frozenset[Keystroke] = frozenset(
    {
        # macOS: force quit / force logout
        Keystroke(key="escape", modifiers=(KeyModifier.META, KeyModifier.ALT)),
        Keystroke(key="q", modifiers=(KeyModifier.META, KeyModifier.ALT, KeyModifier.SHIFT)),
        # macOS: log out
        Keystroke(key="q", modifiers=(KeyModifier.META, KeyModifier.SHIFT)),
        # macOS: lock screen / sleep display
        Keystroke(key="q", modifiers=(KeyModifier.META, KeyModifier.CTRL)),
        # Windows/Linux: task manager, lock, log out
        Keystroke(key="delete", modifiers=(KeyModifier.CTRL, KeyModifier.ALT)),
        # meta+l is a deliberate over-block: it locks the session on Windows
        # and most Linux desktops, but is "focus the address bar" on macOS.
        # One normalized Keystroke cannot mean both, and refusing a useful
        # shortcut is recoverable in a way that locking the user out is not.
        Keystroke(key="l", modifiers=(KeyModifier.META,)),
        Keystroke(key="l", modifiers=(KeyModifier.META, KeyModifier.CTRL)),
        Keystroke(key="delete", modifiers=(KeyModifier.CTRL, KeyModifier.SHIFT)),
        # Windows/Linux: shut down / restart chords
        Keystroke(key="delete", modifiers=(KeyModifier.META, KeyModifier.CTRL, KeyModifier.SHIFT)),
        Keystroke(key="backspace", modifiers=(KeyModifier.CTRL, KeyModifier.ALT)),
        # Linux: kill X session
        Keystroke(
            key="backspace", modifiers=(KeyModifier.CTRL, KeyModifier.ALT, KeyModifier.SHIFT)
        ),
    }
)


@dataclass(frozen=True, slots=True)
class ComputerKeyboardSettings:
    max_type_chars: int = DEFAULT_MAX_TYPE_CHARS

    def __post_init__(self) -> None:
        if self.max_type_chars < 1:
            raise ValueError("ComputerKeyboardSettings.max_type_chars must be positive")


class ComputerKeyboard:
    def __init__(
        self,
        driver: ComputerDriver,
        registry: SnapshotRegistry,
        settings: ComputerKeyboardSettings | None = None,
    ) -> None:
        self._driver = driver
        self._registry = registry
        self._settings = settings or ComputerKeyboardSettings()

    def type_text(self, tool_input: JsonValue, context: ComputerToolContext) -> ToolExecutionResult:
        values = parse_object(tool_input, allowed=_TYPE_TEXT_FIELDS)
        text = _validated_text(values, max_chars=self._settings.max_type_chars)
        window_id = self._fenced_window(values, context)
        self._driver.type_text(text, window_id=window_id)
        # the typed text is already in the approved ToolCall and the durable
        # ToolInvocation input; echoing it again in the output would duplicate
        # it into the brain's context for no benefit
        return ToolExecutionResult.succeeded({"window_id": window_id, "chars": len(text)})

    def press_key(self, tool_input: JsonValue, context: ComputerToolContext) -> ToolExecutionResult:
        values = parse_object(tool_input, allowed=_PRESS_KEY_FIELDS)
        keystroke = _keystroke(values, modifiers=())
        window_id = self._fenced_window(values, context)
        self._driver.press_keystroke(keystroke, window_id=window_id)
        return ToolExecutionResult.succeeded({"window_id": window_id, "key": keystroke.key})

    def hotkey(self, tool_input: JsonValue, context: ComputerToolContext) -> ToolExecutionResult:
        values = parse_object(tool_input, allowed=_HOTKEY_FIELDS)
        keystroke = _keystroke(values, modifiers=_modifiers(values))
        if keystroke in DENIED_HOTKEYS:
            raise HotkeyRejected("this key combination is not permitted")
        window_id = self._fenced_window(values, context)
        self._driver.press_keystroke(keystroke, window_id=window_id)
        return ToolExecutionResult.succeeded(
            {
                "window_id": window_id,
                "key": keystroke.key,
                "modifiers": [modifier.value for modifier in keystroke.modifiers],
                "combination": keystroke.combination,
            }
        )

    def _fenced_window(self, values: dict[str, JsonValue], context: ComputerToolContext) -> str:
        snapshot = resolve_fence(
            values, registry=self._registry, run_scope=context.run_scope, now=context.now
        )
        return snapshot.window.window_id


def _validated_text(values: dict[str, JsonValue], *, max_chars: int) -> str:
    """Bound and screen the payload before any of it reaches a keyboard."""
    text = required_field(values, "text")
    if not isinstance(text, str):
        raise ToolInputInvalid("'text' must be a string")
    if not text:
        raise ToolInputInvalid("'text' must not be empty")
    if len(text) > max_chars:
        raise TextRejected("the requested text exceeds the configured typing limit")
    if "\x00" in text:
        raise TextRejected("the requested text contains a NUL byte")
    if any(_is_disallowed_control(char) for char in text):
        raise TextRejected("the requested text contains disallowed control characters")
    if contains_secret_shape(text):
        raise TextRejected("the requested text looks like it contains a credential")
    return text


def _is_disallowed_control(char: str) -> bool:
    if char in _ALLOWED_TEXT_CONTROLS:
        return False
    return char < " " or char == "\x7f"


def _keystroke(values: dict[str, JsonValue], *, modifiers: tuple[KeyModifier, ...]) -> Keystroke:
    """Build a Keystroke from the closed key allowlist.

    `Keystroke` enforces the allowlist itself; this converts its ValueError
    into the tool-input failure the gateway reports, and names the allowed set
    so a refusal tells Claude what it could have asked for instead.
    """
    key = required_str(values, "key", max_chars=MAX_KEY_CHARS)
    normalized = key.lower()
    if normalized not in ALLOWED_KEYS:
        raise ToolInputInvalid(
            "'key' must be a single [a-z0-9] character or one of "
            f"{sorted(name.value for name in KeyName)}"
        )
    return Keystroke(key=normalized, modifiers=modifiers)


def _modifiers(values: dict[str, JsonValue]) -> tuple[KeyModifier, ...]:
    """Parse modifiers, rejecting duplicates semantically rather than textually.

    `Keystroke` canonicalizes order and dedupes, so a duplicate would otherwise
    pass silently — and an approval fingerprint bound to `["meta","meta"]`
    would then authorize `["meta"]`. Rejecting here keeps one action to one
    spelling.
    """
    raw = string_list(values, "modifiers", max_items=MAX_MODIFIERS)
    parsed: list[KeyModifier] = []
    for entry in raw:
        try:
            modifier = KeyModifier(entry.lower())
        except ValueError:
            allowed = sorted(modifier.value for modifier in KeyModifier)
            raise ToolInputInvalid(f"'modifiers' must contain only {allowed}") from None
        if modifier in parsed:
            raise ToolInputInvalid(f"'modifiers' repeats {modifier.value!r}")
        parsed.append(modifier)
    return tuple(parsed)
