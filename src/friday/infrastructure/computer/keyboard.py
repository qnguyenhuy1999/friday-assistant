"""Keyboard primitives: `computer.type_text`, `computer.press_key`, `computer.hotkey`.

The keyboard is the widest computer-use surface, because a key sequence can
reach things no click can: a shell, a login prompt, a system dialog. So the
allowlists here are closed on every axis at once.

* **Text** is literal, bounded, control-free, and screened for credential
  shapes. The screen is defence in depth — Friday's real position is that it
  never puts a credential where Claude could propose typing it, and there is no
  secret-retrieval path in the system to reach one.
* **Keys** come from `ALLOWED_KEYS` — the named set plus single `[a-z0-9]`
  characters. No raw keycodes, ever: a keycode is an unreviewable integer that
  means whatever the current layout says it means.
* **Hotkeys** are compared as normalized `Keystroke` values against a
  deny-list. Normalization first is what makes the comparison sound —
  `["shift","meta"]` and `["meta","shift"]` are the same combination, and
  substring matching on a rendered string would miss one spelling or refuse an
  innocent superset.

`type_text` inserts text and nothing else: no newline, no tab. Both were
previously allowed as "legitimate in typed prose", and both are really key
*events* wearing text's clothing. A newline in a chat composer sends the
message; in a terminal it runs the line; in a form it may submit. That makes the
character a submit action approved as if it were content — the approval said
"type this", the effect was "type this and commit it". Enter and Tab are
available as `press_key`, where they are proposed and approved as the actions
they are. The driver draws the same line: its text insert is documented as
carrying no special keys.

`type_text` also requires a target. "Type 'hello' into the field labelled
Search" is an approvable sentence; "type 'hello' somewhere in Mail" is not, and
the driver's own guidance is to direct a write at a specific field rather than
at whatever happens to hold focus.

`press_key` and `hotkey` may address the window as a whole. `escape` and
`cmd+c` are directed at a window, not at a control, and demanding a spurious
element for them would push Claude into naming an arbitrary one to satisfy the
schema.

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
from friday.infrastructure.computer.json_shapes import action_json, driver_effect_json
from friday.infrastructure.computer.models import (
    ALLOWED_KEYS,
    KeyModifier,
    KeyName,
    Keystroke,
)
from friday.infrastructure.computer.targets import (
    IDENTITY_FIELDS,
    TARGET_FIELDS,
    TargetResolver,
)
from friday.infrastructure.computer.tool_input import (
    parse_object,
    required_field,
    required_str,
    string_list,
)

DEFAULT_MAX_TYPE_CHARS = 4_096
MAX_MODIFIERS = len(KeyModifier)
MAX_KEY_CHARS = 32

_TYPE_TEXT_FIELDS = IDENTITY_FIELDS | TARGET_FIELDS | {"text"}
_PRESS_KEY_FIELDS = IDENTITY_FIELDS | TARGET_FIELDS | {"key"}
_HOTKEY_FIELDS = IDENTITY_FIELDS | TARGET_FIELDS | {"key", "modifiers"}

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
        resolver: TargetResolver,
        settings: ComputerKeyboardSettings | None = None,
    ) -> None:
        self._driver = driver
        self._resolver = resolver
        self._settings = settings or ComputerKeyboardSettings()

    def type_text(self, tool_input: JsonValue, context: ComputerToolContext) -> ToolExecutionResult:
        values = parse_object(tool_input, allowed=_TYPE_TEXT_FIELDS)
        text = _validated_text(values, max_chars=self._settings.max_type_chars)
        snapshot, target = self._resolver.resolve_addressed(values, now=context.now)
        result = self._driver.type_text(text, target=target)
        # the typed text is already in the approved ToolCall and the durable
        # ToolInvocation input; echoing it again in the output would duplicate
        # it into the brain's context for no benefit
        return ToolExecutionResult.succeeded(
            action_json(
                snapshot,
                target,
                chars=len(text),
                **driver_effect_json(result.effect, result.verified),
            )
        )

    def press_key(self, tool_input: JsonValue, context: ComputerToolContext) -> ToolExecutionResult:
        values = parse_object(tool_input, allowed=_PRESS_KEY_FIELDS)
        keystroke = _keystroke(values, modifiers=())
        snapshot, target = self._resolver.resolve_any(values, now=context.now)
        result = self._driver.press_key(keystroke, target=target)
        return ToolExecutionResult.succeeded(
            action_json(
                snapshot,
                target,
                key=keystroke.key,
                **driver_effect_json(result.effect, result.verified),
            )
        )

    def hotkey(self, tool_input: JsonValue, context: ComputerToolContext) -> ToolExecutionResult:
        values = parse_object(tool_input, allowed=_HOTKEY_FIELDS)
        keystroke = _keystroke(values, modifiers=_modifiers(values))
        if keystroke in DENIED_HOTKEYS:
            raise HotkeyRejected("this key combination is not permitted")
        snapshot, target = self._resolver.resolve_any(values, now=context.now)
        result = self._driver.hotkey(keystroke, target=target)
        return ToolExecutionResult.succeeded(
            action_json(
                snapshot,
                target,
                key=keystroke.key,
                modifiers=[modifier.value for modifier in keystroke.modifiers],
                combination=keystroke.combination,
                **driver_effect_json(result.effect, result.verified),
            )
        )


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
    if any(_is_control(char) for char in text):
        raise TextRejected(
            "the requested text contains control characters; "
            "use computer.press_key for 'enter' or 'tab'"
        )
    if contains_secret_shape(text):
        raise TextRejected("the requested text looks like it contains a credential")
    return text


def _is_control(char: str) -> bool:
    """No exceptions. A newline or tab inside typed text is a key event that an
    approval for content would silently authorize as an action."""
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
