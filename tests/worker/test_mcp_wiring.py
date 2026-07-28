"""MCP composition and worker startup, proved against real child processes.

The property under test is ownership: from the moment an MCP server process
exists, *something* is responsible for reaping it. Startup has a window where
that something is neither `build_mcp_gateway` (already returned) nor
`Worker.close` (no Worker yet) — a duplicate tool name across gateways, an
unreadable config, a broken driver. These tests walk into that window on
purpose and assert no child survives it.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import pytest

from apps.worker.app import create_worker
from friday.domain.approval import ApprovalCategory
from friday.infrastructure.mcp.client import McpRemoteTool
from friday.infrastructure.mcp.config import McpServerConfig, McpToolBinding
from friday.infrastructure.mcp.errors import McpConfigInvalid
from friday.infrastructure.mcp.stdio_client import McpStdioClient
from friday.infrastructure.tools.mcp_composition import McpGatewayConfig, build_mcp_gateway
from friday.infrastructure.tools.mcp_gateway import McpToolGateway
from tests.infrastructure.mcp_fixture_server import make_fixture_server
from tests.worker.fake_claude import make_fake_claude
from tests.worker.test_worker_composition import FINISH, runtime_settings, worker_settings


def _binding(local_name: str = "fixture.read") -> McpToolBinding:
    return McpToolBinding(
        local_name=local_name,
        remote_tool_name="read",
        trusted_description="Read a fixture key.",
        read_only=True,
        approval_required=False,
        approval_category=ApprovalCategory.NETWORK_ACCESS,
    )


def _server(tmp_path: Path, server_id: str, local_name: str = "fixture.read") -> McpServerConfig:
    return McpServerConfig(
        server_id=server_id,
        enabled=True,
        command=make_fixture_server(tmp_path),
        bindings=(_binding(local_name),),
    )


def _alive(pid: int) -> bool:
    for _ in range(50):  # the reap is synchronous, but the OS is not
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        time.sleep(0.02)
    return True


def _child_pids(gateway: McpToolGateway) -> list[int]:
    """The stdio transport owns a pid; the McpClient protocol deliberately
    does not expose one, so the test reaches for the concrete adapter."""
    clients = [cast(McpStdioClient, state.stack.client) for state in gateway._states]  # noqa: SLF001
    return [pid for client in clients if (pid := client.child_pid()) is not None]


@pytest.fixture
def mcp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the worker's MCP settings at a config file the test writes."""
    config_path = tmp_path / "mcp.json"
    monkeypatch.setenv("FRIDAY_MCP_ENABLED", "true")
    monkeypatch.setenv("FRIDAY_MCP_CONFIG_PATH", str(config_path))
    yield config_path


def test_disabled_mcp_composition_performs_no_construction() -> None:
    assert build_mcp_gateway(McpGatewayConfig(enabled=False, servers=())) is None


def test_aggregate_deadline_keeps_unattempted_server_in_health(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    constructed: list[object] = []
    connected: list[str] = []

    class Client:
        def __init__(self, server: McpServerConfig) -> None:
            self.server = server
            constructed.append(self)

        def connect(self, *, timeout_seconds: float | None = None) -> str:
            del timeout_seconds
            connected.append(self.server.server_id)
            return "2025-06-18"

        def list_tools(self, *, timeout_seconds: float | None = None) -> tuple[McpRemoteTool, ...]:
            del timeout_seconds
            return ()

        def call_tool(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("unavailable server must not execute")

        def close(self) -> None:
            pass

    ticks = iter((0.0, 0.0, 121.0, 121.0))
    monkeypatch.setattr("friday.infrastructure.tools.mcp_composition.McpStdioClient", Client)
    gateway = build_mcp_gateway(
        McpGatewayConfig(
            enabled=True,
            servers=(
                _server(tmp_path, "first", "first.read"),
                _server(tmp_path, "second", "second.read"),
            ),
        ),
        monotonic=lambda: next(ticks),
    )

    assert gateway is not None
    assert connected == ["first"]
    assert len(constructed) == 2
    assert [
        (
            health.server_id,
            health.status,
            health.configured_binding_count,
            health.available_binding_count,
            health.failure_code,
        )
        for health in gateway.health()
    ] == [
        ("first", "unavailable", 1, 0, "mcp_connect_timeout"),
        ("second", "unavailable", 1, 0, "mcp_connect_timeout"),
    ]


def test_a_built_gateway_owns_live_children_and_close_reaps_them(tmp_path: Path) -> None:
    gateway = build_mcp_gateway(
        McpGatewayConfig(enabled=True, servers=(_server(tmp_path, "fixture"),))
    )
    assert gateway is not None
    pids = _child_pids(gateway)
    assert len(pids) == 1

    gateway.close()

    assert not _alive(pids[0])


def _raising(message: str) -> Callable[..., object]:
    """A stand-in for a later composition step that fails."""

    def explode(*args: object, **kwargs: object) -> object:
        raise RuntimeError(message)

    return explode


def _record_spawned_children(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Every pid `connect()` brings up, recorded as it happens.

    Observing from the outside is the point: the test must know a child was
    genuinely running before asserting it is gone, or "no orphan" would also
    pass when nothing ever started.
    """
    pids: list[int] = []
    original = McpStdioClient.connect

    def connect(self: McpStdioClient, *, timeout_seconds: float | None = None) -> str:
        version = original(self, timeout_seconds=timeout_seconds)
        pid = self.child_pid()
        if pid is not None:
            pids.append(pid)
        return version

    monkeypatch.setattr(McpStdioClient, "connect", connect)
    return pids


def test_a_failure_inside_composition_reaps_every_started_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A colliding local name is refused before any process is spawned.

    Composition validates the configured names first precisely so this case
    never reaches a subprocess. The assertion that matters is the second one:
    zero children, not "children that were cleaned up afterwards".
    """
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    servers = (
        _server(tmp_path / "a", "first"),
        _server(tmp_path / "b", "second"),  # same local_name: fixture.read
    )

    started = _record_spawned_children(monkeypatch)

    with pytest.raises(McpConfigInvalid, match="more than one binding"):
        build_mcp_gateway(McpGatewayConfig(enabled=True, servers=servers))

    assert started == []


def test_a_discovery_failure_after_a_child_is_up_reaps_every_started_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure lands *after* the first server is already running.

    Two servers, distinct names, so pre-validation passes and both children
    start; the second one's tool list is oversized, and `list_tools` refuses.
    Nothing above `build_mcp_gateway` exists yet to clean either of them up.
    """
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    servers = (
        _server(tmp_path / "a", "first", "fixture.read"),
        _server(tmp_path / "b", "second", "other.read"),
    )
    started = _record_spawned_children(monkeypatch)

    monkeypatch.setattr(
        "friday.infrastructure.tools.mcp_composition.McpToolGateway",
        _raising("composition failed after both children were up"),
    )

    with pytest.raises(RuntimeError, match="composition failed"):
        build_mcp_gateway(McpGatewayConfig(enabled=True, servers=servers))

    assert len(started) == 2  # both children really were running
    assert [pid for pid in started if _alive(pid)] == []


def test_a_startup_failure_after_mcp_started_leaves_no_orphan(
    tmp_path: Path, mcp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`create_worker` fails *after* the MCP child is up.

    This is the window the ExitStack exists for: `build_mcp_gateway` has
    already returned, so its own cleanup is gone, and no Worker exists yet, so
    `Worker.close()` cannot run either. Anything that raises between those two
    points would otherwise leave a live server process behind for good.
    """
    mcp_env.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "server_id": "fixture",
                        "command": list(make_fixture_server(tmp_path)),
                        "bindings": [
                            {
                                "local_name": "fixture.read",
                                "remote_tool_name": "read",
                                "trusted_description": "Read.",
                                "read_only": True,
                                "approval_required": False,
                                "approval_category": "network_access",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    started = _record_spawned_children(monkeypatch)
    executable, _ = make_fake_claude(tmp_path, action_jsons=[FINISH])
    monkeypatch.setattr(
        "apps.worker.app.CompositeToolGateway",
        _raising("composition failed after the MCP child was up"),
    )

    with pytest.raises(RuntimeError, match="composition failed"):
        create_worker(worker_settings(tmp_path), runtime_settings(tmp_path, executable))

    assert len(started) == 1  # the child really was up when composition failed
    assert not _alive(started[0])
