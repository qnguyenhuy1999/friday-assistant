"""McpStdioClient against real MCP server subprocesses.

Framing, timeouts, floods, and process death are exactly the things a mocked
stream gets wrong, so every test here spawns a real child. The fixture server
is protocol-real: it completes the MCP handshake, advertises tools, and can be
told to misbehave in one specific way per test.

The lifecycle tests are the ones that would otherwise be discovered in
production. Discovery deliberately downgrades a failed connect to "this server
is unavailable" and lets the worker keep running for hours — so if `connect()`
left a child behind on a hung handshake, one broken server in an operator's
config would hold a useless process for the worker's entire lifetime, and
nothing would report it.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from friday.domain.approval import ApprovalCategory
from friday.infrastructure.mcp.config import McpServerConfig, McpToolBinding
from friday.infrastructure.mcp.errors import (
    McpCallTimeout,
    McpConnectTimeout,
    McpError,
    McpProtocolError,
    McpRemoteError,
    McpUnavailable,
)
from friday.infrastructure.mcp.stdio_client import McpStdioClient
from tests.infrastructure.mcp_fixture_server import (
    LEAKED_TOKEN,
    FixtureBehaviour,
    make_fixture_server,
)

BINDING = McpToolBinding(
    local_name="fixture.read",
    remote_tool_name="read",
    trusted_description="Fixture read.",
    read_only=True,
    approval_required=False,
    approval_category=ApprovalCategory.NETWORK_ACCESS,
)


@pytest.fixture
def opened() -> Iterator[list[McpStdioClient]]:
    """Guarantees every spawned child is reaped, including on failure."""
    clients: list[McpStdioClient] = []
    yield clients
    for client in clients:
        client.close()


def _client(
    opened: list[McpStdioClient],
    tmp_path: Path,
    behaviour: FixtureBehaviour | None = None,
    *,
    connect_timeout_seconds: float = 10.0,
    call_timeout_seconds: float = 10.0,
    env_from: tuple[str, ...] = (),
    base_environment: dict[str, str] | None = None,
) -> McpStdioClient:
    config = McpServerConfig(
        server_id="fixture",
        enabled=True,
        command=make_fixture_server(tmp_path, behaviour or FixtureBehaviour()),
        bindings=(BINDING,),
        env_from=env_from,
        connect_timeout_seconds=connect_timeout_seconds,
        call_timeout_seconds=call_timeout_seconds,
    )
    client = McpStdioClient(config, base_environment=base_environment)
    opened.append(client)
    return client


def _alive(pid: int | None) -> bool:
    if pid is None:
        return False
    for _ in range(50):  # the reap is synchronous, but the OS is not
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        time.sleep(0.02)
    return True


# --- the happy path -------------------------------------------------------


def test_stdio_client_handshakes_lists_and_calls(
    opened: list[McpStdioClient], tmp_path: Path
) -> None:
    client = _client(opened, tmp_path)

    assert client.connect() == "2025-06-18"
    assert tuple(tool.name for tool in client.list_tools()) == ("read", "write")
    result = client.call_tool("read", {"key": "a"}, timeout_seconds=5)

    assert result.structured_content == {"key": "a", "value": None}


def test_connect_is_idempotent(opened: list[McpStdioClient], tmp_path: Path) -> None:
    client = _client(opened, tmp_path)

    first = client.connect()
    pid = client.child_pid()

    assert client.connect() == first
    assert client.child_pid() == pid  # no second child


# --- lifecycle: a failed connect must leave nothing behind ---------------


@pytest.mark.parametrize(
    ("behaviour", "expected"),
    [
        (FixtureBehaviour(hang_on_start=True), McpConnectTimeout),
        (FixtureBehaviour(malformed_framing=True), McpProtocolError),
        (FixtureBehaviour(protocol_version="1999-01-01"), McpProtocolError),
    ],
    ids=["hang", "malformed", "unsupported-version"],
)
def test_a_failed_handshake_reaps_the_child(
    opened: list[McpStdioClient],
    tmp_path: Path,
    behaviour: FixtureBehaviour,
    expected: type[McpError],
) -> None:
    """Discovery turns this into "unavailable" and keeps going; a surviving
    child would then outlive the failure by the worker's whole lifetime."""
    client = _client(opened, tmp_path, behaviour, connect_timeout_seconds=0.5)

    with pytest.raises(expected):
        client.connect()

    assert not _alive(client.child_pid())


def test_an_unspawnable_command_fails_closed(tmp_path: Path) -> None:
    config = McpServerConfig(
        server_id="fixture",
        enabled=True,
        command=(str(tmp_path / "no-such-mcp-server"),),
        bindings=(BINDING,),
    )
    client = McpStdioClient(config)

    with pytest.raises(McpUnavailable):
        client.connect()
    assert client.child_pid() is None


def test_close_is_safe_before_connect_and_twice(
    opened: list[McpStdioClient], tmp_path: Path
) -> None:
    client = _client(opened, tmp_path)

    client.close()
    client.close()

    with pytest.raises(McpUnavailable):
        client.connect()


def test_close_reaps_the_child(opened: list[McpStdioClient], tmp_path: Path) -> None:
    client = _client(opened, tmp_path)
    client.connect()
    pid = client.child_pid()
    assert pid is not None

    client.close()

    assert not _alive(pid)


# --- bounds ---------------------------------------------------------------


def test_a_stdout_flood_is_bounded_and_becomes_a_failure(
    opened: list[McpStdioClient], tmp_path: Path
) -> None:
    """Line size bounds one message; it does not bound a peer that emits
    unsolicited notifications forever. Memory is not the pressure valve."""
    client = _client(
        opened,
        tmp_path,
        FixtureBehaviour(notification_flood=5_000),
        connect_timeout_seconds=5.0,
        call_timeout_seconds=2.0,
    )
    client.connect()

    with pytest.raises(McpError):
        for _ in range(5):  # the flood outruns the budget within a few calls
            client.call_tool("read", {"key": "a"}, timeout_seconds=1.0)

    assert not _alive(client.child_pid())


def test_an_oversized_tool_list_is_refused(opened: list[McpStdioClient], tmp_path: Path) -> None:
    client = _client(opened, tmp_path, FixtureBehaviour(extra_tool_count=400))
    client.connect()

    with pytest.raises(McpProtocolError):
        client.list_tools()


def test_a_duplicate_tool_name_is_refused_by_discovery(
    opened: list[McpStdioClient], tmp_path: Path
) -> None:
    from friday.infrastructure.mcp.discovery import discover_server

    client = _client(opened, tmp_path, FixtureBehaviour(duplicate_tool_name=True))
    config = McpServerConfig(
        server_id="fixture",
        enabled=True,
        command=make_fixture_server(tmp_path, FixtureBehaviour(duplicate_tool_name=True)),
        bindings=(BINDING,),
    )

    discovery = discover_server(client, config)

    assert discovery.available == ()
    assert discovery.failure_code == "mcp_protocol_error"


# --- failure modes --------------------------------------------------------


def test_a_hanging_call_times_out_and_invalidates_the_session(
    opened: list[McpStdioClient], tmp_path: Path
) -> None:
    client = _client(opened, tmp_path, FixtureBehaviour(hang_on_call=True))
    client.connect()

    with pytest.raises(McpCallTimeout):
        client.call_tool("read", {"key": "a"}, timeout_seconds=0.5)

    # stream alignment is unknowable after a timeout: never reuse it
    with pytest.raises(McpUnavailable):
        client.call_tool("read", {"key": "a"}, timeout_seconds=0.5)


def test_a_cancelled_call_does_not_hang(opened: list[McpStdioClient], tmp_path: Path) -> None:
    client = _client(opened, tmp_path, FixtureBehaviour(hang_on_call=True))
    client.connect()

    with pytest.raises(McpCallTimeout):
        client.call_tool("read", {"key": "a"}, timeout_seconds=30.0, cancelled=lambda: True)


def test_a_server_that_exits_mid_call_is_unavailable(
    opened: list[McpStdioClient], tmp_path: Path
) -> None:
    client = _client(opened, tmp_path, FixtureBehaviour(exit_on_call=True))
    client.connect()

    with pytest.raises(McpUnavailable):
        client.call_tool("read", {"key": "a"}, timeout_seconds=5.0)


def test_a_remote_tool_error_is_reported_without_its_body(
    opened: list[McpStdioClient], tmp_path: Path
) -> None:
    client = _client(opened, tmp_path, FixtureBehaviour(remote_error=True))
    client.connect()

    with pytest.raises(McpRemoteError) as caught:
        client.call_tool("read", {"key": "a"}, timeout_seconds=5.0)

    assert "remote failure" not in str(caught.value)


def test_a_malformed_result_is_a_protocol_error(
    opened: list[McpStdioClient], tmp_path: Path
) -> None:
    client = _client(opened, tmp_path, FixtureBehaviour(malformed_result=True))
    client.connect()

    with pytest.raises(McpProtocolError):
        client.call_tool("read", {"key": "a"}, timeout_seconds=5.0)


@pytest.mark.parametrize(
    "behaviour",
    [
        FixtureBehaviour(result_nan=True),
        FixtureBehaviour(result_infinity=True),
        FixtureBehaviour(duplicate_result_key=True),
    ],
)
def test_non_standard_or_duplicate_json_is_a_protocol_failure(
    opened: list[McpStdioClient], tmp_path: Path, behaviour: FixtureBehaviour
) -> None:
    client = _client(opened, tmp_path, behaviour)
    client.connect()

    with pytest.raises(McpProtocolError):
        client.call_tool("read", {"key": "a"}, timeout_seconds=5.0)


@pytest.mark.parametrize(
    "behaviour",
    [FixtureBehaviour(duplicate_schema_key=True), FixtureBehaviour(schema_nan_minimum=True)],
)
def test_unsafe_schema_json_is_unavailable_during_discovery(
    opened: list[McpStdioClient], tmp_path: Path, behaviour: FixtureBehaviour
) -> None:
    from friday.infrastructure.mcp.discovery import discover_server

    client = _client(opened, tmp_path, behaviour)
    config = McpServerConfig(
        server_id="fixture",
        enabled=True,
        command=make_fixture_server(tmp_path, behaviour),
        bindings=(BINDING,),
    )
    discovery = discover_server(client, config)

    assert discovery.available == ()


def test_malformed_content_block_fails_closed(opened: list[McpStdioClient], tmp_path: Path) -> None:
    client = _client(opened, tmp_path, FixtureBehaviour(malformed_content_block=True))
    client.connect()

    with pytest.raises(McpProtocolError):
        client.call_tool("read", {"key": "a"}, timeout_seconds=5.0)


def test_a_server_that_floods_stderr_still_answers(
    opened: list[McpStdioClient], tmp_path: Path
) -> None:
    """Without the stderr drain this deadlocks on a full pipe, and the symptom
    is indistinguishable from a timeout."""
    client = _client(opened, tmp_path, FixtureBehaviour(stderr_flood_bytes=300_000))
    client.connect()

    result = client.call_tool("read", {"key": "a"}, timeout_seconds=10.0)

    assert result.structured_content == {"key": "a", "value": None}


# --- credential hygiene ---------------------------------------------------


def test_the_child_environment_is_allowlisted(opened: list[McpStdioClient], tmp_path: Path) -> None:
    """A server subprocess must not inherit API keys just because Friday has
    them; only variables the operator named in env_from are passed."""
    base = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", "/tmp"),
        "ANTHROPIC_API_KEY": "sk-must-not-be-inherited",
        "FIXTURE_TOKEN": LEAKED_TOKEN,
    }
    client = _client(
        opened,
        tmp_path,
        FixtureBehaviour(echo_environment=True),
        env_from=("FIXTURE_TOKEN",),
        base_environment=base,
    )
    client.connect()

    result = client.call_tool("read", {"key": "a"}, timeout_seconds=10.0)

    structured = result.structured_content
    assert isinstance(structured, dict)
    names = structured["env"]
    assert isinstance(names, list)
    assert "ANTHROPIC_API_KEY" not in names
    assert "FIXTURE_TOKEN" in names  # explicitly granted, so it is present


def test_a_configured_secret_never_comes_back_out_in_a_result(
    opened: list[McpStdioClient], tmp_path: Path
) -> None:
    """A compromised server can echo the credential Friday handed it. That
    value must not reach a durable record or the brain's context."""
    base = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", "/tmp"),
        "FIXTURE_TOKEN": LEAKED_TOKEN,
    }
    client = _client(
        opened,
        tmp_path,
        FixtureBehaviour(echo_token=True),
        env_from=("FIXTURE_TOKEN",),
        base_environment=base,
    )
    client.connect()

    result = client.call_tool("read", {"key": "a"}, timeout_seconds=10.0)

    assert LEAKED_TOKEN not in repr(result)


def test_the_execution_identity_carries_no_credential_value(
    opened: list[McpStdioClient], tmp_path: Path
) -> None:
    base = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", "/tmp"),
        "FIXTURE_TOKEN": LEAKED_TOKEN,
    }
    client = _client(opened, tmp_path, env_from=("FIXTURE_TOKEN",), base_environment=base)
    client.connect()  # the identity describes what actually got executed

    identity = client.execution_identity()

    assert isinstance(identity, dict)
    assert LEAKED_TOKEN not in repr(identity)
    assert isinstance(identity["principal"], str)
