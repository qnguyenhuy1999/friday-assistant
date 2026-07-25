"""Shared construction for computer-use gateway tests.

Every Phase 13 test needs the same two things: a gateway wired to a
FakeComputerDriver in a throwaway workspace, and control over time — snapshot
TTL is a real fence, so proving it means moving the clock rather than sleeping
through it.

`capture_snapshot` exists so a fencing test can say "given a live capture" in
one line. Tests about *what* the fence rejects should not each re-derive how a
snapshot is created; when they do, a change to capture's output shape breaks
thirty tests that were not about capture.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from friday.application.tool_gateway import (
    ToolCall,
    ToolExecutionRequest,
    ToolExecutionResult,
)
from friday.domain.identifiers import RunId, ToolInvocationId
from friday.domain.json_value import JsonValue
from friday.infrastructure.computer.artifacts import ScreenshotStoreSettings
from friday.infrastructure.computer.capture import ComputerCaptureSettings
from friday.infrastructure.computer.keyboard import ComputerKeyboardSettings
from friday.infrastructure.computer.pointer import ComputerPointerSettings
from friday.infrastructure.computer.snapshots import SnapshotRegistrySettings
from friday.infrastructure.tools.computer_gateway import (
    ComputerToolGateway,
    ComputerToolGatewaySettings,
)
from tests.infrastructure.computer_fakes import FakeComputerDriver

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class MovableClock:
    """A Clock a test can advance. TTL expiry is not observable otherwise."""

    def __init__(self, now: datetime = T0) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)

    def set(self, now: datetime) -> None:
        self._now = now


@dataclass(frozen=True, slots=True)
class Harness:
    gateway: ComputerToolGateway
    driver: FakeComputerDriver
    clock: MovableClock
    workspace: Path
    run_id: RunId

    def run(
        self,
        tool: str,
        tool_input: dict[str, JsonValue] | None = None,
        *,
        run_id: RunId | None = None,
        invocation_id: ToolInvocationId | None = None,
    ) -> ToolExecutionResult:
        return self.gateway.execute(
            ToolExecutionRequest(
                invocation_id=invocation_id or ToolInvocationId.new(),
                run_id=run_id or self.run_id,
                step_id=None,
                call=ToolCall(tool=tool, tool_input=dict(tool_input or {})),
            )
        )

    def capture(
        self, tool_input: dict[str, JsonValue] | None = None, *, run_id: RunId | None = None
    ) -> dict[str, JsonValue]:
        result = self.run("computer.capture", tool_input, run_id=run_id)
        assert result.status == "succeeded", result.failure
        assert isinstance(result.output, dict)
        return result.output

    def capture_snapshot(self, *, run_id: RunId | None = None) -> str:
        """Return a fresh live snapshot_id."""
        snapshot_id = self.capture({"include_screenshot": False}, run_id=run_id)["snapshot_id"]
        assert isinstance(snapshot_id, str)
        return snapshot_id

    def fence(self, snapshot_id: str, window_id: str = "win-mail") -> dict[str, JsonValue]:
        return {"snapshot_id": snapshot_id, "window_id": window_id}


def build_harness(
    workspace: Path,
    *,
    driver: FakeComputerDriver | None = None,
    max_windows: int = 50,
    max_elements: int = 500,
    max_scroll_delta: int = 5_000,
    max_type_chars: int = 4_096,
    ttl_seconds: float = 10.0,
    max_snapshots: int = 32,
    max_snapshots_per_run: int = 8,
    max_capture_bytes: int = 8_000_000,
) -> Harness:
    resolved_driver = driver or FakeComputerDriver()
    clock = MovableClock()
    gateway = ComputerToolGateway(
        ComputerToolGatewaySettings(
            driver=resolved_driver,
            workspace_root=workspace,
            max_windows=max_windows,
            capture=ComputerCaptureSettings(max_elements=max_elements),
            pointer=ComputerPointerSettings(max_scroll_delta=max_scroll_delta),
            keyboard=ComputerKeyboardSettings(max_type_chars=max_type_chars),
            snapshots=SnapshotRegistrySettings(
                ttl_seconds=ttl_seconds,
                max_snapshots=max_snapshots,
                max_snapshots_per_run=max_snapshots_per_run,
            ),
            screenshots=ScreenshotStoreSettings(
                workspace_root=workspace, max_capture_bytes=max_capture_bytes
            ),
            clock=clock,
        )
    )
    return Harness(
        gateway=gateway,
        driver=resolved_driver,
        clock=clock,
        workspace=workspace,
        run_id=RunId.new(),
    )


def output_of(result: ToolExecutionResult) -> dict[str, JsonValue]:
    assert result.status == "succeeded", result.failure
    assert isinstance(result.output, dict)
    return result.output


def failure_code(result: ToolExecutionResult) -> str:
    assert result.status == "failed", result.output
    assert result.failure is not None
    return result.failure.code
