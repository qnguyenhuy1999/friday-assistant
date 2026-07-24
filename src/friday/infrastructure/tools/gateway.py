"""WorkspaceToolGateway — the concrete ToolGateway.

Owns the Phase 11 tool registry and the authoritative risk matrix:

    workspace.list        read-only   no approval
    workspace.read_text   read-only   no approval
    workspace.write_text  mutating    approval required (filesystem_write)
    process.run           high risk   approval required (tool_execution)

`assess` is what the approval-interception path trusts; `execute` maps every
raised policy error onto a structured Failure with a stable code so nothing
OS-specific or unbounded ever reaches the application layer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from friday.application.errors import ToolInputInvalid, ToolNotFound, WorkspaceAccessDenied
from friday.application.tool_gateway import (
    ToolCall,
    ToolDescriptor,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolRiskAssessment,
)
from friday.domain.approval import ApprovalCategory
from friday.domain.failure import Failure, FailureCause
from friday.domain.json_value import JsonValue
from friday.infrastructure.tools.process_runner import ProcessRunner, ProcessRunnerSettings
from friday.infrastructure.tools.workspace_files import WorkspaceFiles, WorkspaceFileSettings
from friday.infrastructure.tools.workspace_paths import resolve_workspace_root


@dataclass(frozen=True, slots=True)
class WorkspaceToolGatewaySettings:
    workspace_root: Path
    max_file_bytes: int
    max_list_entries: int
    process_timeout_seconds: float
    process_max_timeout_seconds: float
    max_stdout_bytes: int
    max_stderr_bytes: int


@dataclass(frozen=True, slots=True)
class _Registration:
    descriptor: ToolDescriptor
    category: ApprovalCategory
    execute: Callable[[JsonValue], ToolExecutionResult]


class WorkspaceToolGateway:
    def __init__(self, settings: WorkspaceToolGatewaySettings) -> None:
        root = resolve_workspace_root(settings.workspace_root)
        files = WorkspaceFiles(
            WorkspaceFileSettings(
                root=root,
                max_file_bytes=settings.max_file_bytes,
                max_list_entries=settings.max_list_entries,
            )
        )
        processes = ProcessRunner(
            ProcessRunnerSettings(
                root=root,
                timeout_seconds=settings.process_timeout_seconds,
                max_timeout_seconds=settings.process_max_timeout_seconds,
                max_stdout_bytes=settings.max_stdout_bytes,
                max_stderr_bytes=settings.max_stderr_bytes,
            )
        )
        self._registry: dict[str, _Registration] = {
            "workspace.list": _Registration(
                descriptor=ToolDescriptor(
                    name="workspace.list",
                    description=(
                        "List workspace entries. Input: {path?: string = '.', "
                        "recursive?: bool = false}."
                    ),
                    read_only=True,
                    approval_required=False,
                ),
                category=ApprovalCategory.TOOL_EXECUTION,
                execute=files.list_entries,
            ),
            "workspace.read_text": _Registration(
                descriptor=ToolDescriptor(
                    name="workspace.read_text",
                    description=(
                        "Read a UTF-8 text file inside the workspace. Input: {path: string}."
                    ),
                    read_only=True,
                    approval_required=False,
                ),
                category=ApprovalCategory.TOOL_EXECUTION,
                execute=files.read_text,
            ),
            "workspace.write_text": _Registration(
                descriptor=ToolDescriptor(
                    name="workspace.write_text",
                    description=(
                        "Create or replace a text file inside the workspace. "
                        "Input: {path: string, content: string, overwrite?: bool = false, "
                        "create_parents?: bool = false}."
                    ),
                    read_only=False,
                    approval_required=True,
                ),
                category=ApprovalCategory.FILESYSTEM_WRITE,
                execute=files.write_text,
            ),
            "process.run": _Registration(
                descriptor=ToolDescriptor(
                    name="process.run",
                    description=(
                        "Run one command (argv list, no shell) inside the workspace. "
                        "Input: {argv: [string, ...], cwd?: string = '.', "
                        "timeout_seconds?: number}."
                    ),
                    read_only=False,
                    approval_required=True,
                ),
                category=ApprovalCategory.TOOL_EXECUTION,
                execute=processes.run,
            ),
        }

    def list_tools(self) -> tuple[ToolDescriptor, ...]:
        return tuple(self._registry[name].descriptor for name in sorted(self._registry))

    def assess(self, call: ToolCall) -> ToolRiskAssessment:
        registration = self._registry.get(call.tool)
        if registration is None:
            raise ToolNotFound(call.tool)
        descriptor = registration.descriptor
        mode = "read-only" if descriptor.read_only else "mutating"
        return ToolRiskAssessment(
            tool=descriptor.name,
            read_only=descriptor.read_only,
            approval_required=descriptor.approval_required,
            category=registration.category,
            summary=f"{descriptor.name} ({mode})",
        )

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        registration = self._registry.get(request.call.tool)
        if registration is None:
            raise ToolNotFound(request.call.tool)
        try:
            return registration.execute(request.call.tool_input)
        except WorkspaceAccessDenied as exc:
            return ToolExecutionResult.failed(
                Failure(
                    code="workspace_escape_rejected",
                    message=str(exc),
                    retryable=False,
                    cause=FailureCause.VALIDATION,
                )
            )
        except ToolInputInvalid as exc:
            return ToolExecutionResult.failed(
                Failure(
                    code="tool_invalid_input",
                    message=str(exc),
                    retryable=False,
                    cause=FailureCause.VALIDATION,
                )
            )
        except OSError:
            # deliberately content-free: OS error text may embed absolute
            # paths outside the workspace
            return ToolExecutionResult.failed(
                Failure(
                    code="tool_execution_failed",
                    message="tool execution failed",
                    retryable=False,
                    cause=FailureCause.TOOL,
                )
            )
