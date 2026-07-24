"""Application-owned tool gateway port and its immutable models. The gateway
owns actual tool execution in infrastructure; the application layer only sees
JSON-compatible values, stable failure codes, and artifact candidates — never
subprocess results, file handles, or OS exception types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from friday.application.errors import ToolInputInvalid
from friday.application.runtime_actions import TOOL_NAME_PATTERN
from friday.domain.approval import ApprovalCategory
from friday.domain.artifact import ArtifactKind
from friday.domain.failure import Failure
from friday.domain.identifiers import RunId, RunStepId, ToolInvocationId
from friday.domain.json_value import JsonValue, ensure_json_value


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    """One registered tool operation, addressed by a dotted name such as
    ``workspace.read_text``."""

    name: str
    description: str
    read_only: bool
    approval_required: bool

    def __post_init__(self) -> None:
        if not TOOL_NAME_PATTERN.match(self.name):
            raise ValueError(f"tool name does not match the required pattern: {self.name!r}")
        if not self.description.strip():
            raise ValueError("tool description must not be empty")


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A proposed tool call, before any invocation exists. Used for risk
    assessment and approval binding."""

    tool: str
    tool_input: JsonValue

    def __post_init__(self) -> None:
        if not TOOL_NAME_PATTERN.match(self.tool):
            raise ToolInputInvalid(f"tool name does not match the required pattern: {self.tool!r}")
        if not isinstance(self.tool_input, dict):
            raise ToolInputInvalid("tool input must be a JSON object")
        ensure_json_value(self.tool_input, path="$.tool_input")


@dataclass(frozen=True, slots=True)
class ToolRiskAssessment:
    """The gateway's authoritative risk verdict for one proposed call."""

    tool: str
    read_only: bool
    approval_required: bool
    category: ApprovalCategory
    summary: str

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError("risk assessment summary must not be empty")


@dataclass(frozen=True, slots=True)
class ArtifactCandidate:
    """Metadata for a file a tool created or replaced inside the workspace,
    ready to be recorded through the Artifact use case. `location` is a
    workspace-relative path — never absolute."""

    kind: ArtifactKind
    name: str
    media_type: str
    location: str
    size: int | None = None
    checksum: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("artifact candidate name must not be empty")
        if not self.media_type.strip():
            raise ValueError("artifact candidate media_type must not be empty")
        if not self.location.strip():
            raise ValueError("artifact candidate location must not be empty")
        if self.location.startswith("/") or self.location.startswith(".."):
            raise ValueError("artifact candidate location must be workspace-relative")
        if self.size is not None and self.size < 0:
            raise ValueError("artifact candidate size must be non-negative")


@dataclass(frozen=True, slots=True)
class ToolExecutionRequest:
    """An authorized call bound to its durable ToolInvocation identity. The
    invocation ID doubles as the idempotency key for the execution."""

    invocation_id: ToolInvocationId
    run_id: RunId
    step_id: RunStepId | None
    call: ToolCall


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    """Bounded, JSON-safe outcome of one tool execution. Failures are
    structured `Failure` values with stable codes — never raw exception
    text, unbounded output, or OS types."""

    status: Literal["succeeded", "failed"]
    output: JsonValue = None
    failure: Failure | None = None
    artifacts: tuple[ArtifactCandidate, ...] = ()

    def __post_init__(self) -> None:
        if self.status == "failed" and self.failure is None:
            raise ValueError("a 'failed' result requires a failure")
        if self.status == "succeeded" and self.failure is not None:
            raise ValueError("a 'succeeded' result must not carry a failure")
        ensure_json_value(self.output, path="$.output")

    @classmethod
    def succeeded(
        cls, output: JsonValue, artifacts: tuple[ArtifactCandidate, ...] = ()
    ) -> ToolExecutionResult:
        return cls(status="succeeded", output=output, artifacts=artifacts)

    @classmethod
    def failed(cls, failure: Failure) -> ToolExecutionResult:
        return cls(status="failed", failure=failure)


class ToolGateway(Protocol):
    def list_tools(self) -> tuple[ToolDescriptor, ...]: ...

    def assess(self, call: ToolCall) -> ToolRiskAssessment: ...

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult: ...
