"""Shared construction for computer-use gateway tests.

Every Phase 13 test needs the same two things: a gateway wired to a
FakeComputerDriver in a throwaway workspace, and a fixed clock so a recorded
`captured_at` is predictable.

There is no snapshot TTL to advance any more. A capture is not a stored fence
with a lifetime: every mutating tool re-captures its window and revalidates the
approved target against what it just saw, so freshness comes from having just
looked. Tests that used to prove expiry now prove *divergence* — the window is
gone, the control is gone, or two controls now match — which is the failure mode
that actually exists.

`element_input` and `pixel_input` exist so a fencing test can say "given an
approved click on Send" in one line. Tests about *what* the fence rejects should
not each re-derive the input shape; when they do, a change to that shape breaks
thirty tests that were not about it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
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
from friday.infrastructure.tools.computer_gateway import (
    ComputerToolGateway,
    ComputerToolGatewaySettings,
)
from tests.infrastructure.computer_fakes import (
    MAIL_PID,
    MAIL_WINDOW_ID,
    FakeComputerDriver,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class FixedClock:
    """A Clock that does not move. Nothing in computer use depends on elapsed
    time any more, so a test that needs a second reading needs a second capture,
    not a later timestamp."""

    def __init__(self, now: datetime = T0) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def identity(*, pid: int = MAIL_PID, window_id: int = MAIL_WINDOW_ID) -> dict[str, JsonValue]:
    return {"pid": pid, "window_id": window_id}


def element_input(
    role: str = "button", label: str = "Send", **extra: JsonValue
) -> dict[str, JsonValue]:
    """Tool input addressing a control by what it is — the approvable form."""
    return {**identity(), "element": {"role": role, "label": label}, **extra}


def pixel_input(x: int = 400, y: int = 300, **extra: JsonValue) -> dict[str, JsonValue]:
    """Tool input addressing a raw window-local screenshot pixel."""
    return {**identity(), "x": x, "y": y, **extra}


@dataclass(frozen=True, slots=True)
class Harness:
    gateway: ComputerToolGateway
    driver: FakeComputerDriver
    clock: FixedClock
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
        merged = {**identity(), **(tool_input or {})}
        result = self.run("computer.capture", merged, run_id=run_id)
        assert result.status == "succeeded", result.failure
        assert isinstance(result.output, dict)
        return result.output

    def click(self, tool_input: dict[str, JsonValue] | None = None) -> ToolExecutionResult:
        return self.run("computer.click", tool_input if tool_input is not None else element_input())


def build_harness(
    workspace: Path,
    *,
    driver: FakeComputerDriver | None = None,
    max_windows: int = 50,
    max_elements: int = 500,
    max_scroll_amount: int = 10,
    max_type_chars: int = 4_096,
    max_capture_bytes: int = 8_000_000,
) -> Harness:
    resolved_driver = driver or FakeComputerDriver()
    clock = FixedClock()
    gateway = ComputerToolGateway(
        ComputerToolGatewaySettings(
            driver=resolved_driver,
            workspace_root=workspace,
            max_windows=max_windows,
            capture=ComputerCaptureSettings(max_elements=max_elements),
            pointer=ComputerPointerSettings(max_scroll_amount=max_scroll_amount),
            keyboard=ComputerKeyboardSettings(max_type_chars=max_type_chars),
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
