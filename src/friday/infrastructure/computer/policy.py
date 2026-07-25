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
the fencing fields (`snapshot_id`, `window_id`) as required: an action that
cannot cite a live capture is refused, and the description has to say so.

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


_FENCE = "Requires {snapshot_id, window_id} naming a live computer.capture result."

COMPUTER_TOOL_POLICY: dict[str, ComputerToolPolicy] = {
    "computer.capture": ComputerToolPolicy(
        description=(
            "Capture one window's accessibility state and a screenshot artifact. "
            "Input: {window_id?: string, include_screenshot?: bool = true, "
            "include_elements?: bool = true, max_elements?: integer}. "
            "Returns a snapshot_id plus numbered elements; every mutating computer "
            "tool must cite that snapshot_id."
        ),
        read_only=True,
        approval_required=False,
    ),
    "computer.pointer_position": ComputerToolPolicy(
        description="Report the current pointer position. Input: {}.",
        read_only=True,
        approval_required=False,
    ),
    "computer.window_list": ComputerToolPolicy(
        description=(
            "List open windows with their bounds. Input: {limit?: integer}. "
            "Results are bounded; `truncated` reports whether any were dropped."
        ),
        read_only=True,
        approval_required=False,
    ),
    "computer.active_window": ComputerToolPolicy(
        description="Report the focused window, or null if nothing is focused. Input: {}.",
        read_only=True,
        approval_required=False,
    ),
    "computer.pointer_move": ComputerToolPolicy(
        description=(
            f"Move the pointer to a captured element or coordinate. {_FENCE} "
            "Input: {snapshot_id, window_id, element?: integer, x?: integer, y?: integer} — "
            "supply either `element` or both `x` and `y`."
        ),
        read_only=False,
        approval_required=True,
    ),
    "computer.click": ComputerToolPolicy(
        description=(
            f"Click a captured element or coordinate. {_FENCE} "
            "Input: {snapshot_id, window_id, element?: integer, x?: integer, y?: integer, "
            "button?: 'left'|'right'|'middle', count?: integer}."
        ),
        read_only=False,
        approval_required=True,
    ),
    "computer.scroll": ComputerToolPolicy(
        description=(
            f"Scroll at a captured element or coordinate. {_FENCE} "
            "Input: {snapshot_id, window_id, element?: integer, x?: integer, y?: integer, "
            "dx?: integer, dy?: integer} — at least one axis must be non-zero."
        ),
        read_only=False,
        approval_required=True,
    ),
    "computer.type_text": ComputerToolPolicy(
        description=(
            f"Type bounded literal text into the focused control. {_FENCE} "
            "Input: {snapshot_id, window_id, text: string}. Credentials are refused."
        ),
        read_only=False,
        approval_required=True,
    ),
    "computer.press_key": ComputerToolPolicy(
        description=(
            f"Press one named key. {_FENCE} "
            "Input: {snapshot_id, window_id, key: string} where `key` is a named key "
            "such as 'enter' or 'escape', or a single character."
        ),
        read_only=False,
        approval_required=True,
    ),
    "computer.hotkey": ComputerToolPolicy(
        description=(
            f"Press one key with modifiers. {_FENCE} "
            "Input: {snapshot_id, window_id, key: string, "
            "modifiers: ['meta'|'ctrl'|'alt'|'shift', ...]}. "
            "Combinations that log out, lock, or force-quit are refused."
        ),
        read_only=False,
        approval_required=True,
    ),
    "computer.focus_window": ComputerToolPolicy(
        description=(
            f"Bring one captured window to the foreground. {_FENCE} "
            "Input: {snapshot_id, window_id}."
        ),
        read_only=False,
        approval_required=True,
    ),
}

READ_ONLY_COMPUTER_TOOLS = tuple(
    sorted(name for name, policy in COMPUTER_TOOL_POLICY.items() if policy.read_only)
)
