"""CompositeToolGateway — a ToolGateway that routes by tool name.

Exists so that each tool family owns its own gateway instead of accreting
into one class: WorkspaceToolGateway keeps workspace/process/memory,
ComputerToolGateway keeps computer.*, and ExecuteToolAction keeps seeing a
single ToolGateway with no idea that more than one exists.

Deliberately policy-free. It does not assess risk, validate input, translate
errors, or reorder verdicts — the gateway that registered a tool is the sole
authority on that tool, and anything else here would create a second place
where risk could be decided.

Routing is a snapshot taken at construction. Member registries are fixed
when they are built, so probing them once is both correct and cheaper than
re-probing on every brain step; a name owned by two members is a composition
bug and fails loudly at construction rather than resolving by argument order.
"""

from __future__ import annotations

from friday.application.errors import ToolNotFound
from friday.application.tool_gateway import (
    ToolCall,
    ToolDescriptor,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolGateway,
    ToolRiskAssessment,
)


class CompositeToolGateway:
    def __init__(self, *gateways: ToolGateway) -> None:
        if not gateways:
            raise ValueError("CompositeToolGateway requires at least one gateway")
        routes: dict[str, ToolGateway] = {}
        descriptors: dict[str, ToolDescriptor] = {}
        duplicates: list[str] = []
        for gateway in gateways:
            for descriptor in gateway.list_tools():
                if descriptor.name in routes:
                    duplicates.append(descriptor.name)
                    continue
                routes[descriptor.name] = gateway
                descriptors[descriptor.name] = descriptor
        if duplicates:
            raise ValueError(
                f"tool name(s) registered by more than one gateway: {sorted(set(duplicates))}"
            )
        self._routes = routes
        self._descriptors = tuple(descriptors[name] for name in sorted(descriptors))

    def list_tools(self) -> tuple[ToolDescriptor, ...]:
        return self._descriptors

    def assess(self, call: ToolCall) -> ToolRiskAssessment:
        return self._route(call.tool).assess(call)

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        return self._route(request.call.tool).execute(request)

    def _route(self, tool: str) -> ToolGateway:
        gateway = self._routes.get(tool)
        if gateway is None:
            raise ToolNotFound(tool)
        return gateway
