"""The authoritative computer-use risk matrix.

One table, reviewable in one screen. Every computer tool Friday is willing to
expose has a row here declaring whether it observes or mutates, whether it
needs human approval, and what its input contract is. A tool with no row is
not registrable, so no desktop capability can become reachable without
passing through this file in review.

Two rules are structural rather than merely tested:

* A mutating tool that does not require approval cannot be constructed.
* Every row is categorized ApprovalCategory.COMPUTER_USE, so a computer-use
  approval can never be satisfied by a filesystem or process approval.

Descriptions are Claude's only schema for these tools, which is why they name
the required identity (`pid`, `window_id`) and spell out how a target is
addressed: an action that cannot name what it is acting on is refused, and the
description has to say so.

**Nine tools, each with a real backing operation.** Three earlier entries were
removed rather than adapted, because a name that promises more than the desktop
delivers is worse than a missing capability:

* `pointer_move` — pointer motion without a click has no faithful backing (the
  driver's cursor move is an overlay in window scope; the real pointer moves
  only in desktop scope, which Friday never captures) and nothing needs it.
* `active_window` — no operation reports the focused window. It would have been
  derived from z-order, and z-order is not keyboard-focus ownership. The raw
  facts (`z_index`, `is_on_screen`, `on_current_space`) are in `window_list`,
  where they are not dressed up as focus.
* `focus_window` — renamed to `bring_to_front`, matching the operation it
  actually performs. The driver documents that operation as explicitly stealing
  foreground, which "focus a window" does not convey.

Deliberately absent, and not merely unimplemented: shell/AppleScript
execution, raw keycodes, clipboard access, credential entry, and OS
permission-dialog automation. Each would collapse the fenced primitive set
into a general-purpose escape hatch.
"""

from __future__ import annotations

from dataclasses import dataclass

from friday.domain.approval import ApprovalCategory


@dataclass(frozen=True, slots=True)
class ComputerToolPolicy:
    description: str
    read_only: bool
    approval_required: bool
    category: ApprovalCategory = ApprovalCategory.COMPUTER_USE

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("ComputerToolPolicy.description must not be empty")
        if not self.read_only and not self.approval_required:
            raise ValueError(
                "a mutating computer tool must require approval; "
                "there is no unprotected path to a desktop side effect"
            )
        if self.read_only and self.approval_required:
            raise ValueError(
                "a read-only computer tool must not require approval; "
                "observation has to stay cheap enough to look before leaping"
            )


_IDENTITY = "Requires {pid, window_id} from computer.window_list."

_TARGET = (
    "Address the target only as element: {role, label} — the role and label survive "
    "the re-capture Friday performs before acting. If no unique semantic element can "
    "be identified, Friday cannot perform that delayed mutation in Phase 13. Do not "
    "use coordinates."
)

_OPTIONAL_TARGET = (
    "When a target is supplied, address it only as element: {role, label}; do not use coordinates."
)

_REVALIDATED = (
    "Friday captures the window again immediately before acting and refuses if the "
    "target is missing (computer_target_not_found), matches more than one control "
    "(computer_target_ambiguous), or the window is gone (computer_window_gone)."
)

COMPUTER_TOOL_POLICY: dict[str, ComputerToolPolicy] = {
    "computer.window_list": ComputerToolPolicy(
        description=(
            "List open windows with their pid, window_id, title, bounds, and stacking "
            "facts (z_index, is_on_screen, on_current_space). Input: {limit?: integer}. "
            "Start here: every other computer tool needs a pid and window_id. "
            "Results are bounded; `truncated` reports whether any were dropped."
        ),
        read_only=True,
        approval_required=False,
    ),
    "computer.capture": ComputerToolPolicy(
        description=(
            "Capture one window's controls and a screenshot artifact. "
            f"{_IDENTITY} "
            "Input: {pid, window_id, include_screenshot?: bool = true, "
            "max_elements?: integer}. "
            "Returns each control's role, label, and pixel frame — the terms a "
            "mutating call is written in. Returns no fence: mutating tools re-capture "
            "for themselves, so there is no id here to cite later."
        ),
        read_only=True,
        approval_required=False,
    ),
    "computer.cursor_position": ComputerToolPolicy(
        description=(
            "Report the OS cursor position in desktop points. Input: {}. "
            "These are NOT window-local screenshot pixels and cannot be used as a "
            "click or scroll target."
        ),
        read_only=True,
        approval_required=False,
    ),
    "computer.click": ComputerToolPolicy(
        description=(
            f"Click a control. {_IDENTITY} {_TARGET} "
            "Input: {pid, window_id, element: {role, label}, "
            "button?: 'left'|'right'|'middle', count?: 1|2}. "
            f"{_REVALIDATED}"
        ),
        read_only=False,
        approval_required=True,
    ),
    "computer.scroll": ComputerToolPolicy(
        description=(
            f"Scroll a window, or a specific region of it. {_IDENTITY} "
            "Input: {pid, window_id, direction: 'up'|'down'|'left'|'right', "
            "amount?: integer, by?: 'line'|'page', element?: {role, label}}. "
            "Omit the target to scroll the window's focused scroller; supply one to "
            "scroll a specific region, which is the only way to reach a nested "
            f"scrollable area. {_OPTIONAL_TARGET} "
            f"{_REVALIDATED}"
        ),
        read_only=False,
        approval_required=True,
    ),
    "computer.type_text": ComputerToolPolicy(
        description=(
            f"Type literal text into one control. {_IDENTITY} {_TARGET} "
            "A target is required — text goes into a named field, not wherever focus "
            "happens to be. "
            "Input: {pid, window_id, element: {role, label}, "
            "text: string}. "
            "Literal characters only: no newline, no tab. Use computer.press_key for "
            "'enter' or 'tab', which are actions rather than content. "
            "Credentials are refused. "
            f"{_REVALIDATED}"
        ),
        read_only=False,
        approval_required=True,
    ),
    "computer.press_key": ComputerToolPolicy(
        description=(
            f"Press one named key. {_IDENTITY} "
            "Input: {pid, window_id, key: string, element?: {role, label}} where `key` "
            "is a named key such as 'enter' or "
            "'escape', or a single character. "
            "The target is optional: omit it to send the key to the window. "
            f"{_OPTIONAL_TARGET} "
            f"{_REVALIDATED}"
        ),
        read_only=False,
        approval_required=True,
    ),
    "computer.hotkey": ComputerToolPolicy(
        description=(
            f"Press one key with modifiers. {_IDENTITY} "
            "Input: {pid, window_id, key: string, "
            "modifiers: ['meta'|'ctrl'|'alt'|'shift', ...], element?: {role, label}}. "
            "The target is optional: omit it to send the combination to the window. "
            f"{_OPTIONAL_TARGET} "
            "Combinations that log out, lock, or force-quit are refused. "
            f"{_REVALIDATED}"
        ),
        read_only=False,
        approval_required=True,
    ),
    "computer.bring_to_front": ComputerToolPolicy(
        description=(
            "Bring one window to the foreground. This genuinely takes foreground away "
            "from whatever the user is in, and changes which application receives "
            "subsequent keystrokes — it is not focus bookkeeping. "
            f"{_IDENTITY} Input: {{pid, window_id}}."
        ),
        read_only=False,
        approval_required=True,
    ),
}

READ_ONLY_COMPUTER_TOOLS = tuple(
    sorted(name for name, policy in COMPUTER_TOOL_POLICY.items() if policy.read_only)
)
