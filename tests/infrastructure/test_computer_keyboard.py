"""Keyboard primitives: bounded text, closed key sets, and the hotkey deny-list.

The keyboard is the widest surface in Phase 13, so these tests lean on closed
sets and negative cases. Three specific things are load-bearing:

* **Nothing reaches the driver on refusal.** Every rejection asserts
  `mutating_calls == ()`; a screened-then-typed payload is not screened.
* **Modifier order does not create a second spelling.** If `["shift","meta"]`
  and `["meta","shift"]` produced different keystrokes, the deny-list could be
  walked around by reordering a list, and so could an approval fingerprint.
* **Raw keycodes are unreachable.** Not merely undocumented — the field does not
  exist, and an integer `key` is refused.

Every mutating call is made directly through `identity()` (window-level target)
or `element_input()` (a labeled control) — there is no separate capture-then-
fence step: each tool re-captures its window and resolves its target as part of
the same call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from friday.application.tool_gateway import ToolCall
from friday.infrastructure.computer.keyboard import DENIED_HOTKEYS
from friday.infrastructure.computer.models import KeyModifier, KeyName, Keystroke
from tests.infrastructure.computer_harness import (
    Harness,
    build_harness,
    element_input,
    failure_code,
    identity,
    output_of,
)

KEYBOARD_TOOLS = ("computer.type_text", "computer.press_key", "computer.hotkey")


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return build_harness(tmp_path)


# --- approval posture -----------------------------------------------------


@pytest.mark.parametrize("tool", KEYBOARD_TOOLS)
def test_keyboard_tools_require_approval(harness: Harness, tool: str) -> None:
    assessment = harness.gateway.assess(ToolCall(tool=tool, tool_input={}))

    assert assessment.read_only is False
    assert assessment.approval_required is True


# --- type_text ------------------------------------------------------------


def test_type_text_types_the_requested_payload(harness: Harness) -> None:
    output = output_of(harness.run("computer.type_text", element_input(text="hello there")))

    assert output["chars"] == 11
    assert harness.driver.only_call("type_text").argument("text") == "hello there"


def test_type_text_does_not_echo_the_payload_back_to_the_brain(harness: Harness) -> None:
    """The text is already in the approved call and the durable invocation
    input; repeating it in the output only duplicates it into context."""
    output = output_of(harness.run("computer.type_text", element_input(text="secret plan")))

    assert "secret plan" not in str(output)


def test_type_text_is_bounded(tmp_path: Path) -> None:
    harness = build_harness(tmp_path, max_type_chars=16)

    rejected = harness.run("computer.type_text", element_input(text="x" * 17))
    accepted = harness.run("computer.type_text", element_input(text="x" * 16))

    assert failure_code(rejected) == "computer_text_rejected"
    assert accepted.status == "succeeded"
    assert len(harness.driver.mutating_calls) == 1


@pytest.mark.parametrize("text", ["", None, 5, True, ["a"], {"a": 1}])
def test_type_text_rejects_empty_payload(harness: Harness, text: object) -> None:
    result = harness.run("computer.type_text", element_input(text=text))  # type: ignore[arg-type]

    assert failure_code(result) == "tool_invalid_input"
    assert harness.driver.mutating_calls == ()


def test_type_text_requires_the_text_field(harness: Harness) -> None:
    result = harness.run("computer.type_text", element_input())

    assert failure_code(result) == "tool_invalid_input"


@pytest.mark.parametrize(
    "text", ["bad\x00nul", "bell\x07here", "esc\x1b[2J", "del\x7f", "\x1b]0;title\x07"]
)
def test_type_text_rejects_control_payload(harness: Harness, text: str) -> None:
    """Escape sequences typed into a terminal are code, not text."""
    result = harness.run("computer.type_text", element_input(text=text))

    assert failure_code(result) == "computer_text_rejected"
    assert harness.driver.mutating_calls == ()


@pytest.mark.parametrize("text", ["line one\nline two", "col\tcol"])
def test_type_text_rejects_newline_and_tab(harness: Harness, text: str) -> None:
    """A newline or tab is a key event wearing text's clothing: it is
    available as `press_key`, proposed and approved as the action it is."""
    result = harness.run("computer.type_text", element_input(text=text))

    assert failure_code(result) == "computer_text_rejected"
    assert harness.driver.mutating_calls == ()


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345",
        "Authorization: Basic dXNlcjpwYXNzd29yZA==",
        # Obviously-redacted placeholders, matching the convention already used
        # in tests/application/test_memory_write_policy.py: these still match the
        # shared detector's provider patterns, but are not literals that secret
        # scanners (ours or GitHub's) should ever have to reason about.
        "ghp_TEST_GITHUB_TOKEN_REDACTED",
        "github_pat_TEST_GITHUB_PAT_REDACTED",
        "sk_live_TEST_STRIPE_KEY_REDACTED",
        "api_key=abcdefghijklmnop",
        "secret=hunter2hunter2",
        "password=correct-horse-battery",
        "access_token=abcdefghijklmnopqrst",
        "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0",
    ],
)
def test_type_text_rejects_secret_shaped_content(harness: Harness, text: str) -> None:
    """Defence in depth. Friday's real position is that it never puts a
    credential where Claude could propose typing it."""
    result = harness.run("computer.type_text", element_input(text=text))

    assert failure_code(result) == "computer_text_rejected"
    assert harness.driver.mutating_calls == ()


@pytest.mark.parametrize(
    "text",
    [
        "Please review the design doc before Friday.",
        "Reply to Dana and say the meeting moved to Thursday",
        "https://example.com/status",
        "notes/meeting.md",
    ],
)
def test_type_text_allows_ordinary_prose(harness: Harness, text: str) -> None:
    """The screen must not be so eager that normal typing becomes impossible."""
    assert harness.run("computer.type_text", element_input(text=text)).status == "succeeded"


@pytest.mark.parametrize(
    "text",
    [
        "See /Users/patrick/Documents/notes/meeting-2026-07-24.md for context",
        "https://example.com/a/fairly/long/url/that/keeps/going/onward",
    ],
)
def test_long_paths_and_urls_are_refused_as_secret_shaped(harness: Harness, text: str) -> None:
    """Documents a real limitation rather than hiding it.

    The shared detector's token class spans `/` and `-`, so a long path or URL
    reads as one high-entropy token. Phase 12's curated memory writes depend on
    that same detector, so this is pinned as known behaviour instead of being
    loosened here — the failure direction is refusal, which is recoverable.
    """
    result = harness.run("computer.type_text", element_input(text=text))

    assert failure_code(result) == "computer_text_rejected"
    assert harness.driver.mutating_calls == ()


# --- press_key ------------------------------------------------------------


@pytest.mark.parametrize("key", [name.value for name in KeyName])
def test_press_key_accepts_every_named_key(harness: Harness, key: str) -> None:
    output = output_of(harness.run("computer.press_key", {**identity(), "key": key}))

    assert output["key"] == key


@pytest.mark.parametrize("key", ["a", "z", "0", "9"])
def test_press_key_accepts_the_single_character_subset(harness: Harness, key: str) -> None:
    assert harness.run("computer.press_key", {**identity(), "key": key}).status == "succeeded"


@pytest.mark.parametrize(
    "key",
    [
        "f13",
        "power",
        "eject",
        "ab",
        "!",
        "",
        "cmd",
        "keycode_53",
        "0x35",
        53,
        True,
        None,
        ["enter"],
    ],
)
def test_press_key_rejects_unknown_key(harness: Harness, key: object) -> None:
    result = harness.run(
        "computer.press_key",
        {**identity(), "key": key},  # type: ignore[dict-item]
    )

    assert failure_code(result) == "tool_invalid_input"
    assert harness.driver.mutating_calls == ()


def test_raw_keycodes_are_not_exposed(harness: Harness) -> None:
    """There is no keycode field to populate, and an integer key is refused —
    a keycode means whatever the active layout says it means."""
    for payload in (
        {"keycode": 53},
        {"key": 53},
        {"key": "enter", "keycode": 36},
        {"key": "enter", "scancode": 28},
        {"key": "\\x1b"},
    ):
        result = harness.run(
            "computer.press_key",
            {**identity(), **payload},  # type: ignore[dict-item]
        )

        assert failure_code(result) == "tool_invalid_input", payload
    assert harness.driver.mutating_calls == ()


# --- hotkey ---------------------------------------------------------------


def test_hotkey_passes_a_normalized_keystroke(harness: Harness) -> None:
    output = output_of(
        harness.run("computer.hotkey", {**identity(), "key": "c", "modifiers": ["meta"]})
    )

    assert output["key"] == "c"
    assert output["modifiers"] == ["meta"]
    assert output["combination"] == "meta+c"
    keystroke = harness.driver.only_call("hotkey").argument("keystroke")
    assert isinstance(keystroke, Keystroke)
    assert keystroke == Keystroke(key="c", modifiers=(KeyModifier.META,))
    assert keystroke.combination == "meta+c"


def test_hotkey_modifiers_are_canonical(harness: Harness) -> None:
    """Two spellings of one combination must produce one keystroke — otherwise
    both the deny-list and the approval fingerprint can be walked around by
    reordering a list."""
    first = output_of(
        harness.run("computer.hotkey", {**identity(), "key": "s", "modifiers": ["shift", "meta"]})
    )
    second = output_of(
        harness.run("computer.hotkey", {**identity(), "key": "s", "modifiers": ["meta", "shift"]})
    )

    assert first["modifiers"] == second["modifiers"] == ["meta", "shift"]
    assert first["combination"] == second["combination"] == "meta+shift+s"


def test_hotkey_modifier_names_tolerate_case_and_surrounding_space(harness: Harness) -> None:
    """`"META "` names the same modifier as `"meta"`, so it resolves to the same
    keystroke. The approval fingerprint still binds the exact spelling, so the
    only asymmetry this creates is an approval being too specific — never too
    permissive."""
    output = output_of(
        harness.run("computer.hotkey", {**identity(), "key": "c", "modifiers": ["META "]})
    )

    assert output["combination"] == "meta+c"


def test_hotkey_rejects_duplicate_modifiers(harness: Harness) -> None:
    """Silent dedup would mean an approval bound to ["meta","meta"] authorizes
    ["meta"] — one action must have one spelling."""
    result = harness.run(
        "computer.hotkey", {**identity(), "key": "c", "modifiers": ["meta", "meta"]}
    )

    assert failure_code(result) == "tool_invalid_input"
    assert harness.driver.mutating_calls == ()


@pytest.mark.parametrize(
    "modifiers", [["command"], ["super"], ["fn"], [""], ["  "], [1], [None], "meta", {"a": 1}]
)
def test_hotkey_modifiers_are_a_closed_set(harness: Harness, modifiers: object) -> None:
    result = harness.run(
        "computer.hotkey",
        {**identity(), "key": "c", "modifiers": modifiers},  # type: ignore[dict-item]
    )

    assert failure_code(result) == "tool_invalid_input"
    assert harness.driver.mutating_calls == ()


DENY_LIST: tuple[Keystroke, ...] = tuple(
    sorted(DENIED_HOTKEYS, key=lambda keystroke: keystroke.combination)
)


@pytest.mark.parametrize("denied", DENY_LIST)
def test_dangerous_hotkeys_are_rejected_before_driver(harness: Harness, denied: Keystroke) -> None:
    """Every deny-list entry, in every modifier order it could be written."""
    result = harness.run(
        "computer.hotkey",
        {
            **identity(),
            "key": denied.key,
            "modifiers": list(reversed([m.value for m in denied.modifiers])),
        },
    )

    assert failure_code(result) == "computer_hotkey_rejected"
    assert harness.driver.mutating_calls == ()


def test_the_deny_list_covers_session_destruction() -> None:
    """Named explicitly so removing an entry is a visible test change rather
    than a silently smaller deny-list."""
    combinations = {keystroke.combination for keystroke in DENIED_HOTKEYS}

    assert "meta+alt+escape" in combinations  # macOS force quit
    assert "meta+shift+q" in combinations  # macOS log out
    assert "meta+ctrl+q" in combinations  # macOS lock screen
    assert "meta+l" in combinations  # Windows/Linux lock
    assert "ctrl+alt+backspace" in combinations  # Linux kill session


def test_an_ordinary_hotkey_is_still_permitted(harness: Harness) -> None:
    """The deny-list must not be so broad that copy and paste are unreachable."""
    for key in ("c", "v", "a", "z"):
        result = harness.run("computer.hotkey", {**identity(), "key": key, "modifiers": ["meta"]})

        assert result.status == "succeeded", key
