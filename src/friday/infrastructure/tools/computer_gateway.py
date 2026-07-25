"""ComputerToolGateway — the only gateway that can reach a ComputerDriver.

Registry, risk verdicts, and failure translation for computer.* tools. The
risk matrix itself lives in friday.infrastructure.computer.policy; this class
binds each declared tool to its handler and refuses to register a tool that
has no policy row, so a capability cannot become reachable without a reviewed
declaration.

Failure translation is one-way and lossy on purpose. A driver's own message
can embed absolute paths, usernames, or window contents, so nothing from a
ComputerUseError or OSError is forwarded: the brain receives a stable code and
a constant message. Timeouts are reported non-retryable because a timed-out
desktop action may already have landed, and retrying a click is not free.

Registration grows task by task. A tool declared in the policy table but not
yet bound to a handler is simply absent from `list_tools()` — Claude cannot
call it, and there is no stub that would answer if it did.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from friday.application.errors import ToolInputInvalid, ToolNotFound
from friday.application.tool_gateway import (
    ToolCall,
    ToolDescriptor,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolRiskAssessment,
)
from friday.domain.failure import Failure, FailureCause
from friday.domain.json_value import JsonValue
from friday.infrastructure.computer.driver import ComputerDriver
from friday.infrastructure.computer.errors import (
    ComputerDriverTimeout,
    ComputerDriverUnavailable,
    ComputerUseError,
)
from friday.infrastructure.computer.observation import (
    ComputerObservation,
    ComputerObservationSettings,
)
from friday.infrastructure.computer.policy import COMPUTER_TOOL_POLICY, ComputerToolPolicy

DEFAULT_MAX_WINDOWS = 50


@dataclass(frozen=True, slots=True)
class ComputerToolGatewaySettings:
    driver: ComputerDriver
    max_windows: int = DEFAULT_MAX_WINDOWS


class ComputerToolGateway:
    def __init__(self, settings: ComputerToolGatewaySettings) -> None:
        observation = ComputerObservation(
            settings.driver, ComputerObservationSettings(max_windows=settings.max_windows)
        )
        self._handlers: dict[str, Callable[[JsonValue], ToolExecutionResult]] = {
            "computer.pointer_position": observation.pointer_position,
            "computer.window_list": observation.window_list,
            "computer.active_window": observation.active_window,
        }
        undeclared = sorted(set(self._handlers) - set(COMPUTER_TOOL_POLICY))
        if undeclared:
            raise ValueError(f"computer tool(s) missing a policy declaration: {undeclared}")
        self._descriptors = tuple(
            ToolDescriptor(
                name=name,
                description=COMPUTER_TOOL_POLICY[name].description,
                read_only=COMPUTER_TOOL_POLICY[name].read_only,
                approval_required=COMPUTER_TOOL_POLICY[name].approval_required,
            )
            for name in sorted(self._handlers)
        )

    def list_tools(self) -> tuple[ToolDescriptor, ...]:
        return self._descriptors

    def assess(self, call: ToolCall) -> ToolRiskAssessment:
        policy = self._policy_for(call.tool)
        mode = "read-only" if policy.read_only else "mutating"
        return ToolRiskAssessment(
            tool=call.tool,
            read_only=policy.read_only,
            approval_required=policy.approval_required,
            category=policy.category,
            summary=f"{call.tool} ({mode}, desktop computer use)",
        )

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        handler = self._handlers.get(request.call.tool)
        if handler is None:
            raise ToolNotFound(request.call.tool)
        if request.cancellation_requested is not None and request.cancellation_requested():
            return ToolExecutionResult.failed(
                Failure(
                    code="claim_lost",
                    message="claim lost before computer use",
                    retryable=True,
                    cause=FailureCause.CANCELLED,
                )
            )
        try:
            return handler(request.call.tool_input)
        except ToolInputInvalid as exc:
            # Friday's own validation text, safe to forward: it describes the
            # requested input, never observed desktop state.
            return _failure("tool_invalid_input", str(exc), FailureCause.VALIDATION)
        except ComputerDriverUnavailable:
            return _failure(
                "computer_driver_unavailable",
                "the computer-use driver is unavailable",
                FailureCause.TOOL,
            )
        except ComputerDriverTimeout:
            return _failure(
                "computer_driver_timeout",
                "the computer-use driver did not respond in time",
                FailureCause.TIMEOUT,
            )
        except ComputerUseError:
            return _failure("computer_use_failed", "computer use failed", FailureCause.TOOL)
        except OSError:
            # deliberately content-free: OS error text can embed absolute
            # paths, usernames, and window contents
            return _failure("computer_use_failed", "computer use failed", FailureCause.TOOL)

    def _policy_for(self, tool: str) -> ComputerToolPolicy:
        if tool not in self._handlers:
            raise ToolNotFound(tool)
        return COMPUTER_TOOL_POLICY[tool]


def _failure(code: str, message: str, cause: FailureCause) -> ToolExecutionResult:
    """Every computer-use failure is non-retryable. Desktop actions are not
    idempotent, and an automatic second attempt could double-apply one that
    already landed."""
    return ToolExecutionResult.failed(
        Failure(code=code, message=message, retryable=False, cause=cause)
    )
