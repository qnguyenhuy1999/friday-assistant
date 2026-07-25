"""McpStdioTransport against real subprocesses.

Framing, timeouts, and process death are exactly the things a mocked stream
gets wrong, so these tests spawn a real child — `sys.executable` running a tiny
scripted server. That is CI-safe (Python is present by definition) and needs no
desktop.

The stderr test is the one that would otherwise be discovered in production: a
child that logs verbosely fills its stderr pipe and blocks forever if nobody
drains it, and the symptom is indistinguishable from a driver timeout.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator

import pytest

from friday.infrastructure.computer.errors import (
    ComputerDriverFailed,
    ComputerDriverTimeout,
    ComputerDriverUnavailable,
)
from friday.infrastructure.computer.mcp_stdio import McpStdioSettings, McpStdioTransport

# A scripted MCP server. `MODE` selects the misbehaviour under test; every mode
# answers `initialize` first so the handshake is not what fails.
_SERVER = r"""
import json, os, sys

MODE = os.environ["MODE"]

def send(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    message = json.loads(line)
    method, request_id = message.get("method"), message.get("id")
    if request_id is None:
        continue  # a notification
    if method == "initialize":
        if MODE == "die_on_initialize":
            sys.exit(3)
        send({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "x"}})
        continue
    if MODE == "silent":
        continue
    if MODE == "die":
        sys.exit(4)
    if MODE == "malformed_json":
        sys.stdout.write("this is not json\n")
        sys.stdout.flush()
        continue
    if MODE == "not_an_object":
        send([1, 2, 3])
        continue
    if MODE == "rpc_error":
        send({"jsonrpc": "2.0", "id": request_id,
              "error": {"code": -32603, "message": "/Users/patrick/.cua/socket failed"}})
        continue
    if MODE == "no_result":
        send({"jsonrpc": "2.0", "id": request_id})
        continue
    if MODE == "huge_line":
        send({"jsonrpc": "2.0", "id": request_id, "result": {"pad": "x" * 200000}})
        continue
    if MODE == "noisy_stderr":
        sys.stderr.write("d" * 300000)
        sys.stderr.flush()
    if MODE == "chatty":
        # unsolicited notifications and a mismatched id before the real answer
        send({"jsonrpc": "2.0", "method": "notifications/message", "params": {}})
        send({"jsonrpc": "2.0", "id": 9999, "result": {"stale": True}})
    if method == "tools/list":
        tools = [{"name": "click"}, {"name": "capture_window"}]
        if MODE == "bad_tool_entry":
            tools.append(7)
        if MODE == "blank_tool_name":
            tools.append({"name": ""})
        send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}})
        continue
    if method == "tools/call":
        if MODE == "tool_error":
            send({"jsonrpc": "2.0", "id": request_id,
                  "result": {"isError": True,
                             "content": [{"type": "text", "text": "/Users/patrick leaked"}]}})
            continue
        if MODE == "structured":
            send({"jsonrpc": "2.0", "id": request_id,
                  "result": {"structuredContent": {"x": 1, "y": 2}}})
            continue
        if MODE == "non_json_text":
            send({"jsonrpc": "2.0", "id": request_id,
                  "result": {"content": [{"type": "text", "text": "not json at all"}]}})
            continue
        if MODE == "no_content":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"content": []}})
            continue
        if MODE == "unusable_content":
            send({"jsonrpc": "2.0", "id": request_id,
                  "result": {"content": [{"type": "image", "data": "zz"}]}})
            continue
        send({"jsonrpc": "2.0", "id": request_id,
              "result": {"content": [{"type": "text",
                                      "text": json.dumps({"echo": message["params"]})}]}})
        continue
    send({"jsonrpc": "2.0", "id": request_id, "result": {}})
"""


def _transport(
    mode: str, *, timeout_seconds: float = 10.0, max_response_bytes: int = 1_000_000
) -> McpStdioTransport:
    return McpStdioTransport(
        McpStdioSettings(
            argv=(sys.executable, "-c", _SERVER),
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
            extra_env={"MODE": mode},
        )
    )


@pytest.fixture
def started() -> Iterator[list[McpStdioTransport]]:
    """Guarantees every spawned child is reaped, including on failure."""
    transports: list[McpStdioTransport] = []
    yield transports
    for transport in transports:
        transport.close()


def _open(
    started: list[McpStdioTransport],
    mode: str,
    *,
    timeout_seconds: float = 10.0,
    max_response_bytes: int = 1_000_000,
) -> McpStdioTransport:
    transport = _transport(
        mode, timeout_seconds=timeout_seconds, max_response_bytes=max_response_bytes
    )
    started.append(transport)
    transport.start()
    return transport


# --- startup --------------------------------------------------------------


def test_start_performs_the_handshake_and_is_idempotent(
    started: list[McpStdioTransport],
) -> None:
    transport = _open(started, "ok")

    transport.start()  # must not re-handshake or raise

    assert transport.list_tool_names() == ("click", "capture_window")


def test_an_unspawnable_command_fails_closed() -> None:
    transport = McpStdioTransport(
        McpStdioSettings(argv=("/nonexistent/friday-cua-driver",), timeout_seconds=2.0)
    )

    with pytest.raises(ComputerDriverUnavailable):
        transport.start()


def test_a_driver_that_dies_during_the_handshake_fails_closed() -> None:
    transport = _transport("die_on_initialize")

    with pytest.raises(ComputerDriverUnavailable):
        transport.start()
    transport.close()


def test_close_is_safe_before_start_and_twice() -> None:
    transport = _transport("ok")

    transport.close()
    transport.close()


# --- tools/list -----------------------------------------------------------


@pytest.mark.parametrize("mode", ["bad_tool_entry", "blank_tool_name"])
def test_a_malformed_tool_list_entry_fails_closed(
    started: list[McpStdioTransport], mode: str
) -> None:
    """Skipping the bad entry would mean preflighting the driver's capabilities
    against an incomplete picture of what it actually exposes."""
    transport = _open(started, mode)

    with pytest.raises(ComputerDriverFailed):
        transport.list_tool_names()


# --- tools/call -----------------------------------------------------------


def test_call_tool_round_trips_arguments_and_decodes_text_content(
    started: list[McpStdioTransport],
) -> None:
    transport = _open(started, "ok")

    result = transport.call_tool("click", {"x": 5, "y": 6})

    assert result == {"echo": {"name": "click", "arguments": {"x": 5, "y": 6}}}


def test_structured_content_is_preferred_when_present(
    started: list[McpStdioTransport],
) -> None:
    transport = _open(started, "structured")

    assert transport.call_tool("pointer_position", {}) == {"x": 1, "y": 2}


def test_notifications_and_stale_ids_are_skipped(started: list[McpStdioTransport]) -> None:
    """An unsolicited notification is not an answer, and neither is a reply to
    someone else's request."""
    transport = _open(started, "chatty")

    result = transport.call_tool("click", {"x": 1, "y": 1})

    assert result == {"echo": {"name": "click", "arguments": {"x": 1, "y": 1}}}


# --- failure modes --------------------------------------------------------


def test_a_silent_driver_times_out(started: list[McpStdioTransport]) -> None:
    transport = _open(started, "silent", timeout_seconds=0.5)

    with pytest.raises(ComputerDriverTimeout):
        transport.call_tool("click", {})


def test_a_driver_that_exits_mid_request_is_unavailable(
    started: list[McpStdioTransport],
) -> None:
    transport = _open(started, "die", timeout_seconds=5.0)

    with pytest.raises(ComputerDriverUnavailable):
        transport.call_tool("click", {})


@pytest.mark.parametrize("mode", ["malformed_json", "not_an_object", "rpc_error", "no_result"])
def test_protocol_violations_are_reported_as_driver_failures(
    started: list[McpStdioTransport], mode: str
) -> None:
    transport = _open(started, mode, timeout_seconds=5.0)

    with pytest.raises(ComputerDriverFailed):
        transport.call_tool("click", {})


def test_a_json_rpc_error_body_is_never_forwarded(started: list[McpStdioTransport]) -> None:
    """The scripted error quotes an absolute path and a username."""
    transport = _open(started, "rpc_error", timeout_seconds=5.0)

    with pytest.raises(ComputerDriverFailed) as caught:
        transport.call_tool("click", {})

    for fragment in ("patrick", "/Users", "-32603", "socket"):
        assert fragment not in str(caught.value)


def test_a_tool_error_result_is_reported_without_its_body(
    started: list[McpStdioTransport],
) -> None:
    transport = _open(started, "tool_error")

    with pytest.raises(ComputerDriverFailed) as caught:
        transport.call_tool("click", {})

    assert "patrick" not in str(caught.value)


@pytest.mark.parametrize("mode", ["non_json_text", "no_content", "unusable_content"])
def test_undecodable_tool_content_is_refused(started: list[McpStdioTransport], mode: str) -> None:
    transport = _open(started, mode)

    with pytest.raises(ComputerDriverFailed):
        transport.call_tool("click", {})


def test_an_oversized_response_line_does_not_hang_or_grow_unbounded(
    started: list[McpStdioTransport],
) -> None:
    """A line that never terminates within the ceiling is a dead connection, not
    something to keep buffering — the peer is a subprocess that could otherwise
    exhaust this process's memory."""
    transport = _open(started, "huge_line", max_response_bytes=4_096, timeout_seconds=5.0)

    with pytest.raises((ComputerDriverUnavailable, ComputerDriverFailed)):
        transport.call_tool("click", {})


def test_a_driver_that_floods_stderr_still_answers(
    started: list[McpStdioTransport],
) -> None:
    """Without the stderr drain this deadlocks on a full pipe and looks exactly
    like a timeout."""
    transport = _open(started, "noisy_stderr", timeout_seconds=15.0)

    result = transport.call_tool("click", {"x": 1, "y": 1})

    assert result == {"echo": {"name": "click", "arguments": {"x": 1, "y": 1}}}


def test_writing_to_a_closed_driver_is_unavailable_not_a_crash() -> None:
    transport = _transport("ok")
    transport.start()
    transport.close()

    with pytest.raises(ComputerDriverUnavailable):
        transport.call_tool("click", {})


# --- environment hygiene --------------------------------------------------


def test_the_child_environment_is_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A driver subprocess must not inherit API keys or nested session state."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-inherited")
    monkeypatch.setenv("CLAUDE_CODE_SESSION", "nested")
    transport = _transport("ok")

    environment = transport._environment()  # noqa: SLF001 - asserting the boundary itself

    assert "ANTHROPIC_API_KEY" not in environment
    assert "CLAUDE_CODE_SESSION" not in environment
    assert environment["MODE"] == "ok"


def test_settings_reject_an_empty_command() -> None:
    for argv in ((), ("",), ("  ",)):
        with pytest.raises(ValueError, match="argv"):
            McpStdioSettings(argv=argv)


@pytest.mark.parametrize(("field", "value"), [("timeout_seconds", 0.0), ("max_response_bytes", 0)])
def test_settings_reject_nonpositive_bounds(field: str, value: object) -> None:
    with pytest.raises(ValueError, match=field):
        McpStdioSettings(argv=("x",), **{field: value})  # type: ignore[arg-type]


def test_a_scripted_server_reply_is_valid_json() -> None:
    """Guards the test double: a syntax error in the embedded server would make
    every test above fail for the wrong reason."""
    compile(_SERVER, "<server>", "exec")
    assert json.dumps({"ok": True})
