"""ComputerToolGateway — the only gateway that can reach a ComputerDriver.

Registry, risk verdicts, and failure translation for computer.* tools. The
risk matrix itself lives in friday.infrastructure.computer.policy; this class
binds each declared tool to its handler and refuses to register a tool that
has no policy row, so a capability cannot become reachable without a reviewed
declaration. It also refuses the reverse — a declared tool with no handler —
because a policy row that nothing implements is a promise in the manifest that
would fail at call time.

This class owns computer-use policy outright. Nothing above it (ExecuteToolAction,
AgentRunProcessor, the worker loop, the brain runtime) and nothing below it (the
driver, the MCP adapter) gets to assess risk, and there is no second place where
a snapshot, an approval, or a claim could be evaluated differently.

Failure translation is one-way and lossy on purpose. A driver's own message
can embed absolute paths, usernames, or window contents, so nothing from a
ComputerUseError or OSError is forwarded: the brain receives a stable code and
a constant message. Friday's *own* refusals are different — a
ComputerActionRejected message describes Friday's policy and the request, never
what was observed — so those are forwarded with the code their class declares.

Timeouts are reported non-retryable because a timed-out desktop action may
already have landed, and retrying a click is not free. For the same reason
*every* computer failure is non-retryable: there is no computer.* operation
where a second automatic attempt is safer than surfacing the ambiguity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from friday.application.errors import ToolInputInvalid, ToolNotFound
from friday.application.ports import Clock
from friday.application.tool_gateway import (
    ToolCall,
    ToolDescriptor,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolRiskAssessment,
)
from friday.domain.failure import Failure, FailureCause
from friday.infrastructure.clock import SystemClock
from friday.infrastructure.computer.artifacts import ScreenshotStore, ScreenshotStoreSettings
from friday.infrastructure.computer.capture import ComputerCapture, ComputerCaptureSettings
from friday.infrastructure.computer.context import ComputerToolContext, ComputerToolHandler
from friday.infrastructure.computer.driver import ComputerDriver
from friday.infrastructure.computer.errors import (
    ComputerActionRejected,
    ComputerDriverTimeout,
    ComputerDriverUnavailable,
    ComputerUseError,
)
from friday.infrastructure.computer.keyboard import ComputerKeyboard, ComputerKeyboardSettings
from friday.infrastructure.computer.observation import (
    ComputerObservation,
    ComputerObservationSettings,
)
from friday.infrastructure.computer.pointer import ComputerPointer, ComputerPointerSettings
from friday.infrastructure.computer.policy import COMPUTER_TOOL_POLICY, ComputerToolPolicy
from friday.infrastructure.computer.targets import TargetResolver, TargetResolverSettings
from friday.infrastructure.computer.windows import ComputerWindows

DEFAULT_MAX_WINDOWS = 50


@dataclass(frozen=True, slots=True)
class ComputerToolGatewaySettings:
    driver: ComputerDriver
    workspace_root: Path
    max_windows: int = DEFAULT_MAX_WINDOWS
    capture: ComputerCaptureSettings = ComputerCaptureSettings()
    pointer: ComputerPointerSettings = ComputerPointerSettings()
    keyboard: ComputerKeyboardSettings = ComputerKeyboardSettings()
    screenshots: ScreenshotStoreSettings | None = None
    clock: Clock = field(default_factory=SystemClock)

    def screenshot_settings(self) -> ScreenshotStoreSettings:
        """Screenshots land inside the workspace unless told otherwise, so the
        common case needs no second path configured — and no path Claude
        supplied can influence where an image is written."""
        if self.screenshots is not None:
            return self.screenshots
        return ScreenshotStoreSettings(workspace_root=self.workspace_root)


class ComputerToolGateway:
    def __init__(self, settings: ComputerToolGatewaySettings) -> None:
        driver = settings.driver
        self._driver = driver
        self._clock = settings.clock
        # One resolver, shared by every mutating handler: the capture-then-check
        # sequence is the fence, and a second implementation of it is a second
        # answer to "was this action still in bounds?"
        resolver = TargetResolver(
            driver, TargetResolverSettings(max_elements=settings.capture.max_elements)
        )
        observation = ComputerObservation(
            driver, ComputerObservationSettings(max_windows=settings.max_windows)
        )
        capture = ComputerCapture(
            driver,
            ScreenshotStore(settings.screenshot_settings()),
            settings.capture,
        )
        pointer = ComputerPointer(driver, resolver, settings.pointer)
        keyboard = ComputerKeyboard(driver, resolver, settings.keyboard)
        windows = ComputerWindows(driver, resolver)

        self._handlers: dict[str, ComputerToolHandler] = {
            # read-only observation
            "computer.window_list": observation.window_list,
            "computer.capture": capture.capture,
            "computer.cursor_position": observation.cursor_position,
            # mutating input — every one of these requires approval
            "computer.click": pointer.click,
            "computer.scroll": pointer.scroll,
            "computer.type_text": keyboard.type_text,
            "computer.press_key": keyboard.press_key,
            "computer.hotkey": keyboard.hotkey,
            "computer.bring_to_front": windows.bring_to_front,
        }
        undeclared = sorted(set(self._handlers) - set(COMPUTER_TOOL_POLICY))
        if undeclared:
            raise ValueError(f"computer tool(s) missing a policy declaration: {undeclared}")
        unimplemented = sorted(set(COMPUTER_TOOL_POLICY) - set(self._handlers))
        if unimplemented:
            raise ValueError(f"declared computer tool(s) with no handler: {unimplemented}")
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
            # cheap defence in depth. The authoritative fence is the durable
            # claim check ExecuteToolAction performs immediately before this
            # call; this only shortens the window, it does not replace it.
            return ToolExecutionResult.failed(
                Failure(
                    code="claim_lost",
                    message="claim lost before computer use",
                    retryable=True,
                    cause=FailureCause.CANCELLED,
                )
            )
        context = ComputerToolContext(
            run_scope=str(request.run_id),
            invocation_id=str(request.invocation_id),
            now=self._clock.now(),
        )
        try:
            return handler(request.call.tool_input, context)
        except ToolInputInvalid as exc:
            # Friday's own validation text, safe to forward: it describes the
            # requested input, never observed desktop state.
            return _failure("tool_invalid_input", str(exc), FailureCause.VALIDATION)
        except ComputerActionRejected as exc:
            # a fence refused before any driver call; the message is Friday's
            # own policy text and each subclass declares its stable code
            return _failure(exc.code, str(exc), FailureCause.VALIDATION)
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
        except ValueError:
            # a driver returned data its own value objects reject — malformed
            # infrastructure output, not a Claude mistake, and its text may
            # quote the offending observed value
            return _failure(
                "computer_use_failed",
                "the computer-use driver returned invalid data",
                FailureCause.TOOL,
            )
        except OSError:
            # deliberately content-free: OS error text can embed absolute
            # paths, usernames, and window contents
            return _failure("computer_use_failed", "computer use failed", FailureCause.TOOL)

    def close(self) -> None:
        """Shut the driver down.

        The gateway constructed the driver's transport, so the gateway is what
        can release it. Without this the worker exits leaving an orphaned
        driver process holding a stdio pipe.
        """
        self._driver.close()

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
