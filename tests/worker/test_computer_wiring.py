"""Production configuration and composition for computer use.

The properties that matter most here are about the *off* state, because off is
the default and therefore the state almost every deployment runs in:

* no driver object is constructed,
* no cua-driver process is spawned,
* zero `computer.*` tools reach the manifest.

"Registers no tools" is the one Claude can observe. If a disabled deployment
still advertised `computer.click`, Claude would propose desktop actions that
could only ever fail, and would waste turns rediscovering that.

On the enabled side, the load-bearing assertion is that the manifest and the
execution path are the *same object*. Two registries could disagree about which
tools exist or what they cost, and the disagreement would be resolved by
whichever one the approval check happened to consult.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from apps.worker.computer_settings import ComputerSettings
from apps.worker.runtime_settings import RuntimeSettings
from apps.worker.settings import WorkerSettings
from friday.application.tool_gateway import ToolGateway
from friday.domain.json_value import JsonValue
from friday.infrastructure.computer.mcp_stdio import McpToolResult, McpTransport
from friday.infrastructure.tools.composite import CompositeToolGateway
from friday.infrastructure.tools.computer_composition import (
    ComputerGatewayConfig,
    ComputerUseUnavailable,
    _driver_environment,
    build_computer_gateway,
    check_computer_driver,
)
from friday.infrastructure.tools.gateway import (
    WorkspaceToolGateway,
    WorkspaceToolGatewaySettings,
)

COMPUTER_ENV = (
    "FRIDAY_COMPUTER_USE_ENABLED",
    "FRIDAY_CUA_DRIVER_CMD",
    "FRIDAY_COMPUTER_TIMEOUT_SECONDS",
    "FRIDAY_COMPUTER_MAX_CAPTURE_BYTES",
    "FRIDAY_COMPUTER_MAX_TYPE_CHARS",
    "FRIDAY_COMPUTER_MAX_SCROLL_AMOUNT",
    "FRIDAY_COMPUTER_MAX_ELEMENTS",
    "FRIDAY_CUA_TELEMETRY_ENABLED",
)

EXPECTED_COMPUTER_TOOLS = (
    "computer.bring_to_front",
    "computer.capture",
    "computer.click",
    "computer.cursor_position",
    "computer.hotkey",
    "computer.press_key",
    "computer.scroll",
    "computer.type_text",
    "computer.window_list",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings are read from the environment, so a stray variable in the
    developer's shell must not decide what these tests assert."""
    for name in COMPUTER_ENV:
        monkeypatch.delenv(name, raising=False)


# --- settings -------------------------------------------------------------


def test_computer_use_defaults_disabled() -> None:
    """Computer use is the only capability that can act on the human's own
    session, outside any workspace confinement. Opting in must be deliberate."""
    settings = ComputerSettings.from_env()

    assert settings.computer_use_enabled is False


def test_the_defaults_are_usable_without_any_other_configuration() -> None:
    settings = ComputerSettings.from_env()

    assert settings.driver_command == ("cua-driver", "mcp")
    assert settings.max_type_chars == 4_096
    assert settings.max_scroll_amount == 10
    assert settings.max_capture_bytes == 8_000_000
    assert settings.max_elements == 500
    assert settings.telemetry_enabled is False


def test_telemetry_defaults_off_and_is_stated_explicitly() -> None:
    """An unset variable is a default the *driver* chooses; Friday's default is
    off, so it is passed down rather than omitted."""
    config = _config(enabled=True, telemetry_enabled=False)

    assert config.telemetry_enabled is False
    assert _driver_environment(config) == {"CUA_DRIVER_RS_TELEMETRY_ENABLED": "false"}


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_computer_use_can_be_enabled_explicitly(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("FRIDAY_COMPUTER_USE_ENABLED", value)

    assert ComputerSettings.from_env().computer_use_enabled is True


def test_a_non_boolean_enable_flag_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRIDAY_COMPUTER_USE_ENABLED", "maybe")

    with pytest.raises(ValueError, match="must be a boolean"):
        ComputerSettings.from_env()


def test_the_driver_command_is_parsed_as_argv_without_a_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quoting is honoured so a path with a space works; nothing is handed to a
    shell, so no metacharacter in this string is ever interpreted."""
    monkeypatch.setenv("FRIDAY_CUA_DRIVER_CMD", '"/opt/my tools/cua-driver" --serve')

    assert ComputerSettings.from_env().driver_command == (
        "/opt/my tools/cua-driver",
        "--serve",
    )


def test_an_unparseable_driver_command_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRIDAY_CUA_DRIVER_CMD", 'cua-driver "unterminated')

    with pytest.raises(ValueError, match="valid command line"):
        ComputerSettings.from_env()


def test_an_empty_driver_command_is_rejected_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validating an unused command would turn "off" into a startup error."""
    monkeypatch.setenv("FRIDAY_CUA_DRIVER_CMD", "   ")
    assert ComputerSettings.from_env().computer_use_enabled is False

    monkeypatch.setenv("FRIDAY_COMPUTER_USE_ENABLED", "true")
    with pytest.raises(ValueError, match="driver_command"):
        ComputerSettings.from_env()


@pytest.mark.parametrize(
    "name",
    [
        "FRIDAY_COMPUTER_TIMEOUT_SECONDS",
        "FRIDAY_COMPUTER_MAX_CAPTURE_BYTES",
        "FRIDAY_COMPUTER_MAX_TYPE_CHARS",
        "FRIDAY_COMPUTER_MAX_SCROLL_AMOUNT",
        "FRIDAY_COMPUTER_MAX_ELEMENTS",
    ],
)
def test_every_limit_must_be_positive(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "0")

    with pytest.raises(ValueError, match="must be positive"):
        ComputerSettings.from_env()


def test_the_scroll_ceiling_cannot_exceed_the_representable_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRIDAY_COMPUTER_MAX_SCROLL_AMOUNT", "51")

    with pytest.raises(ValueError, match="must not exceed 50 notches"):
        ComputerSettings.from_env()


# --- composition ----------------------------------------------------------


class RecordingTransport:
    """Stands in for a spawned cua-driver so nothing real is launched."""

    instances: list[RecordingTransport] = []

    def __init__(self, tool_names: tuple[str, ...], *, healthy: bool = True) -> None:
        self.tool_names = tool_names
        self.healthy = healthy
        self.started = 0
        self.closed = 0
        RecordingTransport.instances.append(self)

    def start(self) -> None:
        self.started += 1

    def list_tool_names(self) -> tuple[str, ...]:
        return self.tool_names

    def call_tool(
        self, name: str, arguments: Mapping[str, JsonValue], *, require_payload: bool = True
    ) -> McpToolResult:
        del name, arguments, require_payload
        return McpToolResult()

    def close(self) -> None:
        self.closed += 1


def _config(
    *, enabled: bool, workspace: Path | None = None, **overrides: object
) -> ComputerGatewayConfig:
    defaults: dict[str, object] = {
        "enabled": enabled,
        "workspace_root": workspace or Path("."),
        "driver_command": ("cua-driver",),
        "timeout_seconds": 15.0,
        "max_capture_bytes": 8_000_000,
        "max_type_chars": 4_096,
        "max_scroll_amount": 10,
    }
    defaults.update(overrides)
    return ComputerGatewayConfig(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def healthy_transport(monkeypatch: pytest.MonkeyPatch) -> type[RecordingTransport]:
    """Patch the transport constructor the factory uses, so `build_computer_gateway`
    runs its real code path without spawning anything."""
    from friday.infrastructure.computer.cua_driver import CuaToolNames

    RecordingTransport.instances.clear()
    names = CuaToolNames().all_names()
    monkeypatch.setattr(
        "friday.infrastructure.tools.computer_composition.McpStdioTransport",
        lambda settings: RecordingTransport(names),
    )
    return RecordingTransport


def test_disabled_computer_use_constructs_no_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing is spawned and nothing is constructed — not a driver that reports
    itself unhealthy, but no driver at all."""
    spawned: list[object] = []
    monkeypatch.setattr(
        "friday.infrastructure.tools.computer_composition.McpStdioTransport",
        lambda settings: spawned.append(settings),
    )

    assert build_computer_gateway(_config(enabled=False)) is None
    assert spawned == []


def test_disabled_computer_use_registers_zero_computer_tools(tmp_path: Path) -> None:
    gateway = _composite(tmp_path, computer=None)

    assert [name for name in _tool_names(gateway) if name.startswith("computer.")] == []


def test_enabled_computer_use_registers_computer_tools(
    tmp_path: Path, healthy_transport: type[RecordingTransport]
) -> None:
    computer = build_computer_gateway(_config(enabled=True, workspace=tmp_path))

    assert computer is not None
    assert _tool_names(computer) == EXPECTED_COMPUTER_TOOLS
    assert len(healthy_transport.instances) == 1
    assert healthy_transport.instances[0].started >= 1


def test_enabled_unavailable_driver_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Startup is the right place to discover this. The alternative is a worker
    that claims Runs and fails every desktop action it is asked to perform."""
    RecordingTransport.instances.clear()
    monkeypatch.setattr(
        "friday.infrastructure.tools.computer_composition.McpStdioTransport",
        lambda settings: RecordingTransport(("click",)),  # missing nine required tools
    )

    with pytest.raises(ComputerUseUnavailable, match="enabled but unavailable"):
        build_computer_gateway(_config(enabled=True))

    # and the half-started driver is not left running
    assert RecordingTransport.instances[0].closed == 1


def test_a_broken_enabled_configuration_never_silently_downgrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicitly enabled must never resolve to disabled: that would be Friday
    deciding on the operator's behalf that the desktop does not matter."""
    monkeypatch.setattr(
        "friday.infrastructure.tools.computer_composition.McpStdioTransport",
        lambda settings: RecordingTransport(()),
    )

    with pytest.raises(ComputerUseUnavailable):
        build_computer_gateway(_config(enabled=True))


def test_an_enabled_config_requires_a_driver_command() -> None:
    with pytest.raises(ValueError, match="driver_command"):
        _config(enabled=True, driver_command=())


def test_a_disabled_config_tolerates_an_absent_driver_command() -> None:
    assert _config(enabled=False, driver_command=()).enabled is False


def test_preflight_checks_the_driver_without_leaving_it_running(
    healthy_transport: type[RecordingTransport],
) -> None:
    health = check_computer_driver(_config(enabled=True))

    assert health.available is True
    assert healthy_transport.instances[0].closed == 1


def test_preflight_reports_disabled_without_spawning(monkeypatch: pytest.MonkeyPatch) -> None:
    spawned: list[object] = []
    monkeypatch.setattr(
        "friday.infrastructure.tools.computer_composition.McpStdioTransport",
        lambda settings: spawned.append(settings),
    )

    health = check_computer_driver(_config(enabled=False))

    assert health.available is True
    assert health.detail == "disabled"
    assert spawned == []


# --- one composite for manifest and execution -----------------------------


def _worker_settings(tmp_path: Path) -> WorkerSettings:
    from datetime import timedelta

    from apps.worker.settings import WorkerSettings

    return WorkerSettings(
        database_url=f"sqlite:///{tmp_path / 'wiring.db'}",
        worker_id="wiring-worker",
        lease_duration=timedelta(seconds=60),
        candidate_limit=10,
        poll_interval_seconds=0.01,
        heartbeat_interval_seconds=0.05,
        maintenance_interval_seconds=0.05,
        maintenance_batch_size=100,
        retry_max_attempts=3,
        retry_base_delay=timedelta(seconds=5),
        retry_multiplier=2.0,
        retry_max_delay=timedelta(seconds=300),
    )


def _runtime_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RuntimeSettings:
    from apps.worker.runtime_settings import RuntimeSettings
    from tests.worker.fake_claude import make_fake_claude

    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    # memory is a separate opt-in; keep it out of this test's manifest
    monkeypatch.delenv("FRIDAY_MEMORY_ENABLED", raising=False)
    return RuntimeSettings(
        workspace_root=workspace,
        brain_backend="claude_cli",
        claude_executable=make_fake_claude(
            tmp_path,
            action_jsons=['{"version": 1, "action": "finish", "result": {"summary": "ok"}}'],
        )[0],
        claude_model=None,
        claude_timeout_seconds=5.0,
        claude_max_output_bytes=100_000,
        claude_max_stderr_bytes=100_000,
        max_turns_per_claim=4,
        max_tool_calls_per_claim=4,
        max_context_chars=4_000,
        max_response_bytes=100_000,
        max_yield_seconds=60,
        max_processing_seconds=30.0,
        tool_max_file_bytes=1_000,
        tool_max_list_entries=10,
        tool_timeout_seconds=1.0,
        tool_max_timeout_seconds=2.0,
        tool_max_stdout_bytes=1_000,
        tool_max_stderr_bytes=1_000,
    )


def _workspace_gateway(workspace: Path) -> WorkspaceToolGateway:
    return WorkspaceToolGateway(
        WorkspaceToolGatewaySettings(
            workspace_root=workspace,
            max_file_bytes=1_000,
            max_list_entries=10,
            process_timeout_seconds=1.0,
            process_max_timeout_seconds=2.0,
            max_stdout_bytes=1_000,
            max_stderr_bytes=1_000,
        )
    )


def _composite(workspace: Path, *, computer: ToolGateway | None) -> CompositeToolGateway:
    gateways: list[ToolGateway] = [_workspace_gateway(workspace)]
    if computer is not None:
        gateways.append(computer)
    return CompositeToolGateway(*gateways)


def _tool_names(gateway: ToolGateway) -> tuple[str, ...]:
    return tuple(descriptor.name for descriptor in gateway.list_tools())


@pytest.mark.parametrize("computer_enabled", [False, True])
def test_worker_uses_one_composite_gateway_for_manifest_and_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    healthy_transport: type[RecordingTransport],
    computer_enabled: bool,
) -> None:
    """Object identity, not "two equivalent registries".

    The brain is offered `gateway.list_tools()` and the approval check consults
    `gateway.assess()`. If those came from different instances, a tool could be
    advertised by one and unknown to the other — and which one won would depend
    on where in the call path you looked. Asserting `is` makes the disagreement
    unrepresentable rather than merely unlikely.
    """
    from apps.worker.app import create_worker

    monkeypatch.setenv("FRIDAY_COMPUTER_USE_ENABLED", "true" if computer_enabled else "false")
    worker = create_worker(_worker_settings(tmp_path), _runtime_settings(tmp_path, monkeypatch))

    processor = worker.processor
    manifest_gateway = processor._gateway  # noqa: SLF001 - asserting the wiring itself
    execution_gateway = processor._execute_tool_action._gateway  # noqa: SLF001

    assert manifest_gateway is execution_gateway
    assert isinstance(manifest_gateway, CompositeToolGateway)
    names = _tool_names(manifest_gateway)
    assert "workspace.write_text" in names
    assert ("computer.click" in names) is computer_enabled
    worker.engine.dispose()


def test_a_broken_enabled_configuration_stops_worker_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No Worker exists, so no Run can be claimed by a worker that cannot do
    what its manifest promises."""
    from apps.worker.app import create_worker

    RecordingTransport.instances.clear()
    monkeypatch.setattr(
        "friday.infrastructure.tools.computer_composition.McpStdioTransport",
        lambda settings: RecordingTransport(("click",)),
    )
    monkeypatch.setenv("FRIDAY_COMPUTER_USE_ENABLED", "true")

    with pytest.raises(ComputerUseUnavailable):
        create_worker(_worker_settings(tmp_path), _runtime_settings(tmp_path, monkeypatch))


def test_workspace_tools_remain_available_when_computer_use_disabled(tmp_path: Path) -> None:
    names = _tool_names(_composite(tmp_path, computer=None))

    for tool in ("workspace.list", "workspace.read_text", "workspace.write_text", "process.run"):
        assert tool in names


def test_workspace_tools_remain_available_when_computer_use_is_enabled(
    tmp_path: Path, healthy_transport: type[RecordingTransport]
) -> None:
    """Adding a gateway must not displace an existing one."""
    computer = build_computer_gateway(_config(enabled=True, workspace=tmp_path))
    names = _tool_names(_composite(tmp_path, computer=computer))

    for tool in ("workspace.list", "workspace.write_text", "process.run", "computer.click"):
        assert tool in names


def test_memory_tools_remain_available_after_composite_gateway_wiring(
    tmp_path: Path, healthy_transport: type[RecordingTransport]
) -> None:
    """Phase 12 must not regress: memory tools live on the workspace gateway, and
    routing through a composite has to leave them reachable."""
    from friday.application.memory.models import MemoryVaultPolicy
    from friday.infrastructure.tools.memory_tools import MemoryToolSettings

    vault = tmp_path / "vault"
    (vault / "Friday" / "Inbox").mkdir(parents=True)
    workspace = WorkspaceToolGateway(
        WorkspaceToolGatewaySettings(
            workspace_root=tmp_path,
            max_file_bytes=1_000,
            max_list_entries=10,
            process_timeout_seconds=1.0,
            process_max_timeout_seconds=2.0,
            max_stdout_bytes=1_000,
            max_stderr_bytes=1_000,
            memory=MemoryToolSettings(
                vault_root=vault,
                policy=MemoryVaultPolicy(
                    include_globs=("**/*.md",),
                    exclude_globs=(),
                    max_files=10,
                    max_note_bytes=1_000,
                ),
                max_search_limit=10,
                max_excerpt_chars=500,
                managed_root="Friday",
            ),
        )
    )
    computer = build_computer_gateway(_config(enabled=True, workspace=tmp_path))
    assert computer is not None
    names = _tool_names(CompositeToolGateway(workspace, computer))

    for tool in ("memory.search", "memory.read_note", "memory.create_note", "computer.capture"):
        assert tool in names


def test_the_composite_refuses_a_duplicate_tool_name(
    tmp_path: Path, healthy_transport: type[RecordingTransport]
) -> None:
    """Composition bug, not a runtime coin flip resolved by argument order."""
    computer = build_computer_gateway(_config(enabled=True, workspace=tmp_path))
    assert computer is not None

    with pytest.raises(ValueError, match="more than one gateway"):
        CompositeToolGateway(computer, computer)


def test_the_computer_manifest_is_deterministically_sorted(
    tmp_path: Path, healthy_transport: type[RecordingTransport]
) -> None:
    computer = build_computer_gateway(_config(enabled=True, workspace=tmp_path))
    assert computer is not None
    names = _tool_names(computer)

    assert list(names) == sorted(names)
    assert len(names) == 9


def test_the_transport_protocol_is_satisfied_by_the_recording_double() -> None:
    """Guards the double: if McpTransport gained a method, these tests would
    otherwise keep passing against a stale stand-in."""
    transport: McpTransport = RecordingTransport(())

    assert transport.list_tool_names() == ()
