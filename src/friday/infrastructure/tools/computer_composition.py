"""The one intentional bridge from a composition root into the desktop.

`apps.worker.app` must be able to add a computer gateway to its composite
without learning what a ComputerDriver is, how MCP framing works, or that
cua-driver is a subprocess. So this module — and only this module, alongside
computer_gateway.py — imports `friday.infrastructure.computer`, and exposes a
config dataclass of primitives plus two functions.

That narrowness is the point. The alternative, letting the worker import driver
internals directly, would mean the architecture test's authorized-importer list
grows every time a capability is added, until it documents nothing. Here the
list stays at two files and the boundary keeps its meaning:

    apps.worker.app
        -> build_computer_gateway(config)          # this module
            -> McpStdioTransport / CuaDriverComputerDriver / SnapshotRegistry
            -> ComputerToolGateway

Config is primitives, not settings objects, so the module never imports
`apps.*` — infrastructure does not depend on a composition root. `ComputerSettings`
in apps/worker maps env vars onto this shape; nothing else here knows env exists.

**Default off, and fail closed when on.** Disabled means no driver is
constructed, no cua-driver process is spawned, and `None` is returned so zero
computer.* tools reach the manifest. Enabled-but-broken raises during startup,
before any Run can be claimed — never a silent downgrade to a healthy-looking
gateway whose every call would fail, and never a fallback to some other input
mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from friday.infrastructure.computer.artifacts import ScreenshotStoreSettings
from friday.infrastructure.computer.capture import ComputerCaptureSettings
from friday.infrastructure.computer.cua_driver import (
    CuaDriverComputerDriver,
    CuaDriverSettings,
)
from friday.infrastructure.computer.driver import ComputerDriverHealth
from friday.infrastructure.computer.errors import ComputerDriverUnavailable
from friday.infrastructure.computer.keyboard import ComputerKeyboardSettings
from friday.infrastructure.computer.mcp_stdio import McpStdioSettings, McpStdioTransport
from friday.infrastructure.computer.models import DEFAULT_MAX_ELEMENTS
from friday.infrastructure.computer.pointer import ComputerPointerSettings
from friday.infrastructure.computer.snapshots import SnapshotRegistrySettings
from friday.infrastructure.tools.computer_gateway import (
    ComputerToolGateway,
    ComputerToolGatewaySettings,
)

ComputerUseUnavailable = ComputerDriverUnavailable
"""Re-exported so preflight can report a broken configuration without importing
the computer package itself."""

_TELEMETRY_VARIABLES = ("CUA_TELEMETRY", "CUA_TELEMETRY_ENABLED")


@dataclass(frozen=True, slots=True)
class ComputerGatewayConfig:
    """Primitive-only description of a production computer-use stack."""

    enabled: bool
    workspace_root: Path
    driver_command: tuple[str, ...]
    timeout_seconds: float
    max_capture_bytes: int
    max_type_chars: int
    max_scroll_delta: int
    capture_ttl_seconds: float
    max_snapshots: int
    max_elements: int = DEFAULT_MAX_ELEMENTS
    telemetry_enabled: bool = False

    def __post_init__(self) -> None:
        if not self.enabled:
            # nothing else is load-bearing when disabled, and validating an
            # unused command would turn "off" into a configuration error
            return
        if not self.driver_command or not all(part.strip() for part in self.driver_command):
            raise ValueError("driver_command must be a non-empty argument list")


def build_computer_gateway(config: ComputerGatewayConfig) -> ComputerToolGateway | None:
    """Construct the production computer gateway, or None when disabled.

    Raises ComputerUseUnavailable when computer use is explicitly enabled but
    the driver cannot be started or does not expose the tools Friday calls.
    Startup is the right place to find that out: the alternative is a worker
    that claims Runs and fails every desktop action.
    """
    if not config.enabled:
        return None
    driver = _build_driver(config)
    health = driver.health()
    if not health.available:
        driver.close()
        raise ComputerUseUnavailable(f"computer use is enabled but unavailable: {health.detail}")
    return ComputerToolGateway(
        ComputerToolGatewaySettings(
            driver=driver,
            workspace_root=config.workspace_root,
            capture=ComputerCaptureSettings(max_elements=config.max_elements),
            pointer=ComputerPointerSettings(max_scroll_delta=config.max_scroll_delta),
            keyboard=ComputerKeyboardSettings(max_type_chars=config.max_type_chars),
            snapshots=SnapshotRegistrySettings(
                ttl_seconds=config.capture_ttl_seconds,
                max_snapshots=config.max_snapshots,
                max_snapshots_per_run=min(config.max_snapshots, 8),
            ),
            screenshots=ScreenshotStoreSettings(
                workspace_root=config.workspace_root,
                max_capture_bytes=config.max_capture_bytes,
            ),
        )
    )


def check_computer_driver(config: ComputerGatewayConfig) -> ComputerDriverHealth:
    """Preflight the driver without building a gateway or keeping it running."""
    if not config.enabled:
        return ComputerDriverHealth(available=True, detail="disabled")
    driver = _build_driver(config)
    try:
        return driver.health()
    finally:
        driver.close()


def _build_driver(config: ComputerGatewayConfig) -> CuaDriverComputerDriver:
    return CuaDriverComputerDriver(
        McpStdioTransport(
            McpStdioSettings(
                argv=config.driver_command,
                timeout_seconds=config.timeout_seconds,
                extra_env=_driver_environment(config),
            )
        ),
        CuaDriverSettings(max_capture_bytes=config.max_capture_bytes),
    )


def _driver_environment(config: ComputerGatewayConfig) -> dict[str, str]:
    """Opt the driver out of telemetry explicitly rather than by omission.

    Both spellings are set because an unset variable is a default the driver
    chooses, and Friday's default here is off.
    """
    value = "1" if config.telemetry_enabled else "0"
    return {name: value for name in _TELEMETRY_VARIABLES}
