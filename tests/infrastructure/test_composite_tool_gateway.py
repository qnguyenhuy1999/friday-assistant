"""CompositeToolGateway: name-based routing across several gateways.

The composite exists so that adding a tool family does not mean growing
WorkspaceToolGateway into a god object. It must stay a router: no risk
policy, no input validation, no error translation of its own — every verdict
belongs to the gateway that registered the tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from friday.application.errors import ToolInputInvalid, ToolNotFound
from friday.application.tool_gateway import (
    ToolCall,
    ToolDescriptor,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolGateway,
    ToolRiskAssessment,
)
from friday.domain.approval import ApprovalCategory
from friday.domain.identifiers import RunId, ToolInvocationId
from friday.infrastructure.tools.composite import CompositeToolGateway


@dataclass(slots=True)
class StubGateway:
    """A ToolGateway that only records what it was asked to do."""

    tools: tuple[str, ...]
    label: str = "stub"
    raises: Exception | None = None
    list_tools_calls: int = 0
    assessed: list[str] = field(default_factory=list)
    executed: list[str] = field(default_factory=list)

    def list_tools(self) -> tuple[ToolDescriptor, ...]:
        self.list_tools_calls += 1
        return tuple(
            ToolDescriptor(
                name=name,
                description=f"{name} from {self.label}",
                read_only=True,
                approval_required=False,
            )
            for name in self.tools
        )

    def assess(self, call: ToolCall) -> ToolRiskAssessment:
        self.assessed.append(call.tool)
        if self.raises is not None:
            raise self.raises
        return ToolRiskAssessment(
            tool=call.tool,
            read_only=True,
            approval_required=False,
            category=ApprovalCategory.TOOL_EXECUTION,
            summary=f"{call.tool} via {self.label}",
        )

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        self.executed.append(request.call.tool)
        if self.raises is not None:
            raise self.raises
        return ToolExecutionResult.succeeded({"handled_by": self.label})


def request_for(tool: str) -> ToolExecutionRequest:
    return ToolExecutionRequest(
        invocation_id=ToolInvocationId.new(),
        run_id=RunId.new(),
        step_id=None,
        call=ToolCall(tool=tool, tool_input={}),
    )


@pytest.fixture
def workspace() -> StubGateway:
    return StubGateway(tools=("workspace.list", "process.run"), label="workspace")


@pytest.fixture
def computer() -> StubGateway:
    return StubGateway(tools=("computer.capture", "computer.click"), label="computer")


@pytest.fixture
def composite(workspace: StubGateway, computer: StubGateway) -> CompositeToolGateway:
    return CompositeToolGateway(workspace, computer)


# --- shape ----------------------------------------------------------------


def test_composite_satisfies_the_tool_gateway_port(composite: CompositeToolGateway) -> None:
    port: ToolGateway = composite

    assert len(port.list_tools()) == 4


def test_manifest_merges_members_in_deterministic_name_order(
    composite: CompositeToolGateway,
) -> None:
    """The manifest goes into the brain prompt, so ordering must not depend on
    which gateway happened to be constructed first."""
    assert [descriptor.name for descriptor in composite.list_tools()] == [
        "computer.capture",
        "computer.click",
        "process.run",
        "workspace.list",
    ]


def test_manifest_order_is_independent_of_member_order(
    workspace: StubGateway, computer: StubGateway
) -> None:
    forward = CompositeToolGateway(workspace, computer).list_tools()
    reversed_members = CompositeToolGateway(
        StubGateway(tools=computer.tools, label="computer"),
        StubGateway(tools=workspace.tools, label="workspace"),
    ).list_tools()

    assert [d.name for d in forward] == [d.name for d in reversed_members]


def test_descriptors_are_passed_through_untouched(composite: CompositeToolGateway) -> None:
    descriptions = {d.name: d.description for d in composite.list_tools()}

    assert descriptions["computer.click"] == "computer.click from computer"
    assert descriptions["workspace.list"] == "workspace.list from workspace"


# --- construction guards --------------------------------------------------


def test_duplicate_tool_names_across_members_are_rejected_at_construction() -> None:
    """Ambiguous routing must be impossible, not resolved by member order."""
    with pytest.raises(ValueError, match="registered by more than one gateway"):
        CompositeToolGateway(
            StubGateway(tools=("workspace.list", "computer.click"), label="a"),
            StubGateway(tools=("computer.click",), label="b"),
        )


def test_the_duplicate_error_names_the_offending_tool() -> None:
    with pytest.raises(ValueError, match="computer.click"):
        CompositeToolGateway(
            StubGateway(tools=("computer.click",), label="a"),
            StubGateway(tools=("computer.click",), label="b"),
        )


def test_a_composite_must_have_at_least_one_member() -> None:
    with pytest.raises(ValueError, match="at least one gateway"):
        CompositeToolGateway()


def test_members_are_probed_once_at_construction_not_per_call(
    composite: CompositeToolGateway, workspace: StubGateway
) -> None:
    """Routing is a snapshot: a member's registry is fixed at construction, so
    re-probing per call would be pure overhead on every brain step."""
    composite.assess(ToolCall(tool="workspace.list", tool_input={}))
    composite.execute(request_for("workspace.list"))
    composite.list_tools()

    assert workspace.list_tools_calls == 1


# --- routing --------------------------------------------------------------


def test_assess_is_routed_to_the_registering_gateway(
    composite: CompositeToolGateway, workspace: StubGateway, computer: StubGateway
) -> None:
    assessment = composite.assess(ToolCall(tool="computer.click", tool_input={}))

    assert assessment.summary == "computer.click via computer"
    assert computer.assessed == ["computer.click"]
    assert workspace.assessed == []


def test_execute_is_routed_to_exactly_one_gateway(
    composite: CompositeToolGateway, workspace: StubGateway, computer: StubGateway
) -> None:
    result = composite.execute(request_for("process.run"))

    assert result.output == {"handled_by": "workspace"}
    assert workspace.executed == ["process.run"]
    assert computer.executed == []


@pytest.mark.parametrize("tool", ["computer.capture", "computer.click"])
def test_every_registered_tool_is_reachable(composite: CompositeToolGateway, tool: str) -> None:
    assert composite.assess(ToolCall(tool=tool, tool_input={})).tool == tool


def test_unknown_tools_raise_tool_not_found_from_both_entry_points(
    composite: CompositeToolGateway, workspace: StubGateway, computer: StubGateway
) -> None:
    with pytest.raises(ToolNotFound):
        composite.assess(ToolCall(tool="browser.click", tool_input={}))
    with pytest.raises(ToolNotFound):
        composite.execute(request_for("browser.click"))
    assert workspace.assessed == [] and computer.assessed == []
    assert workspace.executed == [] and computer.executed == []


def test_a_member_error_propagates_unchanged() -> None:
    """The composite must not translate failures — the owning gateway is the
    only place that knows what a failure means."""
    failing = StubGateway(
        tools=("computer.click",), label="computer", raises=ToolInputInvalid("bad point")
    )
    composite = CompositeToolGateway(failing)

    with pytest.raises(ToolInputInvalid, match="bad point"):
        composite.assess(ToolCall(tool="computer.click", tool_input={}))
    with pytest.raises(ToolInputInvalid, match="bad point"):
        composite.execute(request_for("computer.click"))


def test_a_single_member_composite_is_transparent(workspace: StubGateway) -> None:
    """The computer-use-disabled shape: composing just the workspace gateway
    must behave exactly like using it directly."""
    composite = CompositeToolGateway(workspace)

    assert [d.name for d in composite.list_tools()] == ["process.run", "workspace.list"]
    assert composite.execute(request_for("workspace.list")).status == "succeeded"
