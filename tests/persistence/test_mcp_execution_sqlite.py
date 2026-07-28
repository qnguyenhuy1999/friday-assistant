"""Production-path E2E for MCP, against real SQLite and a real child process.

Nothing is stubbed: a migrated database, the real ExecuteToolAction transaction
discipline, the real gateway, and an actual MCP server subprocess speaking
JSON-RPC over stdio. The claims Phase 18 makes are all about what survives the
transaction boundaries and the process boundary, which is exactly what an
in-process test with a fake client cannot show.

What each test pins down:

* a read-only binding executes and durably records vendor-free provenance;
* a mutating binding parks for approval, performs **no** remote call, and after
  one human approval performs exactly **one** — verified against the server's
  own state, not against Friday's belief about it;
* an approval granted for one binding does not authorize a different input;
* a claim lost before the call produces zero side effects.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from friday.application.approval_workflow import ApproveRequest
from friday.application.claim_aware_tool_execution import ExecuteToolAction
from friday.application.commands import ApproveRequestCommand, RequestApprovalCommand
from friday.application.errors import ToolExecutionAmbiguous
from friday.application.ports import UnitOfWorkFactory
from friday.application.runtime_context import RunSnapshot, build_runtime_context
from friday.application.tool_authorization import (
    RequestToolApproval,
    compute_legacy_authorization_fingerprint,
)
from friday.application.tool_gateway import ToolCall, ToolExecutionRequest
from friday.domain.approval import ApprovalCategory, ApprovalRequest, ApprovalStatus
from friday.domain.identifiers import ApprovalRequestId, RunId, TaskId, ToolInvocationId
from friday.domain.run import Run, RunStatus
from friday.domain.task import Task
from friday.domain.tool import ToolInvocation, ToolInvocationStatus
from friday.infrastructure.mcp.config import McpServerConfig, McpToolBinding
from friday.infrastructure.mcp.discovery import discover_server
from friday.infrastructure.mcp.stdio_client import McpStdioClient
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory
from friday.infrastructure.tools.mcp_gateway import McpServerStack, McpToolGateway
from tests.infrastructure.mcp_fixture_server import FixtureBehaviour, make_fixture_server

T0 = datetime.fromisoformat("2026-07-28T12:00:00+00:00")
LEASE = timedelta(minutes=1)
WORKER = "w1"
TOKEN = "tok"

READ = ToolCall(tool="fixture.read", tool_input={"key": "k"})
WRITE = ToolCall(tool="fixture.write", tool_input={"key": "k", "value": "v"})


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def _bindings() -> tuple[McpToolBinding, ...]:
    return (
        McpToolBinding(
            local_name="fixture.read",
            remote_tool_name="read",
            trusted_description="Read a fixture key.",
            read_only=True,
            approval_required=False,
            approval_category=ApprovalCategory.NETWORK_ACCESS,
        ),
        McpToolBinding(
            local_name="fixture.write",
            remote_tool_name="write",
            trusted_description="Write a fixture key.",
            read_only=False,
            approval_required=True,
            approval_category=ApprovalCategory.NETWORK_ACCESS,
        ),
    )


@pytest.fixture
def uow_factory(tmp_path: Path) -> Iterator[UnitOfWorkFactory]:
    db_path = tmp_path / "mcp-e2e.db"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    yield create_unit_of_work_factory(create_session_factory(engine))
    engine.dispose()


@pytest.fixture
def gateway(tmp_path: Path) -> Iterator[McpToolGateway]:
    """A gateway over a real MCP child process, discovered exactly as at startup."""
    server = McpServerConfig(
        server_id="fixture",
        enabled=True,
        command=make_fixture_server(tmp_path),
        bindings=_bindings(),
    )
    client = McpStdioClient(server)
    discovery = discover_server(client, server)
    assert discovery.failure_code is None
    assert len(discovery.available) == 2
    built = McpToolGateway((McpServerStack(server, client, discovery),))
    yield built
    built.close()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(T0)


def _seed_claimed_run(uow_factory: UnitOfWorkFactory, run_id: RunId) -> None:
    task = Task.new(
        id=TaskId.new(), title="call an external service", description="", created_at=T0
    )
    task.start(T0)
    with uow_factory() as uow:
        uow.tasks.add(task)
        uow.commit()
    run = Run.new(id=run_id, task_id=task.id, created_at=T0)
    run.start(T0)
    with uow_factory() as uow:
        uow.runs.add(run)
        uow.commit()
    with uow_factory() as uow:
        uow.work_queue.enqueue(run_id, available_at=T0, enqueued_at=T0)
        assert uow.work_queue.try_claim(run_id, WORKER, TOKEN, T0, T0 + LEASE)
        uow.commit()


def _generation(uow_factory: UnitOfWorkFactory, run_id: RunId) -> int:
    with uow_factory() as uow:
        item = uow.work_queue.get(run_id)
        assert item is not None
        return item.claim_generation


def _reclaim(uow_factory: UnitOfWorkFactory, run_id: RunId) -> int:
    with uow_factory() as uow:
        run = uow.runs.get(run_id)
        assert run is not None and run.status is RunStatus.RUNNING
        assert uow.work_queue.try_claim(run_id, WORKER, TOKEN, T0, T0 + LEASE)
        uow.commit()
    return _generation(uow_factory, run_id)


def _remote_value(gateway: McpToolGateway, key: str) -> object:
    """Ask the server itself what it stored — the only honest witness to a
    side effect. Friday's own records could agree with each other and still be
    wrong about what crossed the process boundary."""
    result = gateway.execute(
        _request(RunId.new(), ToolCall(tool="fixture.read", tool_input={"key": key}))
    )
    assert result.status == "succeeded"
    output = result.output
    assert isinstance(output, dict)
    structured = output["structured"]
    assert isinstance(structured, dict)
    return structured["value"]


def _request(run_id: RunId, call: ToolCall) -> ToolExecutionRequest:
    from friday.domain.identifiers import ToolInvocationId

    return ToolExecutionRequest(ToolInvocationId.new(), run_id, None, call)


def _approved_legacy(run_id: RunId, call: ToolCall) -> ApprovalRequest:
    approval = ApprovalRequest.new(
        id=ApprovalRequestId.new(),
        run_id=run_id,
        category=ApprovalCategory.NETWORK_ACCESS,
        summary="legacy",
        reason="legacy replay proof",
        requested_action=call.tool,
        requested_input=call.tool_input,
        requested_at=T0,
        authorization_fingerprint=compute_legacy_authorization_fingerprint(
            run_id=run_id, step_id=None, call=call
        ),
    )
    approval.approve(T0, "operator")
    approval.consume(T0)
    return approval


def _gateway_with_behaviour(
    tmp_path: Path,
    behaviour: FixtureBehaviour,
    *,
    base_environment: dict[str, str] | None = None,
    call_timeout_seconds: float = 10.0,
) -> McpToolGateway:
    server = McpServerConfig(
        server_id="fixture",
        enabled=True,
        command=make_fixture_server(tmp_path, behaviour),
        bindings=_bindings(),
        call_timeout_seconds=call_timeout_seconds,
        env_from=("FIXTURE_TOKEN",) if base_environment is not None else (),
    )
    client = McpStdioClient(server, base_environment=base_environment)
    discovery = discover_server(client, server)
    assert discovery.failure_code is None
    return McpToolGateway((McpServerStack(server, client, discovery),))


def test_a_read_only_binding_executes_and_records_vendor_free_provenance(
    uow_factory: UnitOfWorkFactory, gateway: McpToolGateway, clock: FixedClock
) -> None:
    run_id = RunId.new()
    _seed_claimed_run(uow_factory, run_id)

    outcome = ExecuteToolAction(uow_factory, clock, gateway).execute(
        run_id=run_id,
        step_id=None,
        call=READ,
        worker_id=WORKER,
        claim_token=TOKEN,
        claim_generation=_generation(uow_factory, run_id),
    )

    assert outcome.kind == "executed"
    with uow_factory() as fresh:
        invocations = fresh.tool_invocations.list_for_run(run_id)
        assert len(invocations) == 1
        invocation = invocations[0]
        assert invocation.status is ToolInvocationStatus.SUCCEEDED
        assert invocation.tool_name == "fixture.read"
        assert invocation.approval_request_id is None
        provenance = invocation.provenance
        assert provenance is not None
        assert provenance.kind == "mcp"
        assert provenance.target == "fixture"
        assert provenance.remote_name == "read"
        assert len(provenance.binding_fingerprint) == 64
        # no vendor name, argv, or credential reaches the durable record
        assert "fixture-server" not in json.dumps(invocation.output)


def test_a_mutating_binding_needs_approval_and_then_calls_exactly_once(
    uow_factory: UnitOfWorkFactory, gateway: McpToolGateway, clock: FixedClock
) -> None:
    run_id = RunId.new()
    _seed_claimed_run(uow_factory, run_id)
    executor = ExecuteToolAction(uow_factory, clock, gateway)

    # --- proposed: parked, and the remote service is untouched ------------
    first = executor.execute(
        run_id=run_id,
        step_id=None,
        call=WRITE,
        worker_id=WORKER,
        claim_token=TOKEN,
        claim_generation=_generation(uow_factory, run_id),
    )
    assert first.kind == "approval_required"
    assert first.risk.authorization_scope is not None
    assert _remote_value(gateway, "k") is None
    with uow_factory() as fresh:
        assert fresh.tool_invocations.list_for_run(run_id) == []

    # --- Friday durably requests approval for that exact action -----------
    approval = RequestToolApproval(uow_factory, clock).execute(
        RequestApprovalCommand(
            run_id=run_id,
            category=first.risk.category,
            summary=first.risk.summary,
            reason="writing to an external service",
            requested_action=WRITE.tool,
            requested_input=WRITE.tool_input,
            authorization_fingerprint=first.fingerprint,
        ),
        worker_id=WORKER,
        claim_token=TOKEN,
        claim_generation=_generation(uow_factory, run_id),
    )
    with uow_factory() as fresh:
        run = fresh.runs.get(run_id)
        assert run is not None and run.status is RunStatus.WAITING_FOR_APPROVAL
        assert fresh.work_queue.get(run_id) is None  # parked, not runnable
    assert _remote_value(gateway, "k") is None

    # --- a human approves, the run resumes, the call happens once ---------
    ApproveRequest(uow_factory, clock).execute(
        ApproveRequestCommand(approval_id=approval.approval_id, resolver="patrick")
    )
    generation = _reclaim(uow_factory, run_id)
    second = executor.execute(
        run_id=run_id,
        step_id=None,
        call=WRITE,
        worker_id=WORKER,
        claim_token=TOKEN,
        claim_generation=generation,
    )
    assert second.kind == "executed"
    assert second.replayed is False
    assert _remote_value(gateway, "k") == "v"

    with uow_factory() as fresh:
        invocations = [
            invocation
            for invocation in fresh.tool_invocations.list_for_run(run_id)
            if invocation.tool_name == "fixture.write"
        ]
        assert len(invocations) == 1
        assert invocations[0].status is ToolInvocationStatus.SUCCEEDED
        assert invocations[0].approval_request_id == approval.approval_id
        stored = fresh.approvals.list_for_run(run_id)
        assert len(stored) == 1
        assert stored[0].status is ApprovalStatus.APPROVED
        assert stored[0].is_consumed is True
        assert stored[0].authorization_fingerprint == first.fingerprint

    # --- and a repeat proposal replays rather than writing again ----------
    replay = executor.execute(
        run_id=run_id,
        step_id=None,
        call=WRITE,
        worker_id=WORKER,
        claim_token=TOKEN,
        claim_generation=generation,
    )
    assert replay.replayed is True


def test_an_approved_write_does_not_authorize_a_different_write(
    uow_factory: UnitOfWorkFactory, gateway: McpToolGateway, clock: FixedClock
) -> None:
    """An approval a human granted for one key does not authorize another."""
    run_id = RunId.new()
    _seed_claimed_run(uow_factory, run_id)
    executor = ExecuteToolAction(uow_factory, clock, gateway)

    first = executor.execute(
        run_id=run_id,
        step_id=None,
        call=WRITE,
        worker_id=WORKER,
        claim_token=TOKEN,
        claim_generation=_generation(uow_factory, run_id),
    )
    approval = RequestToolApproval(uow_factory, clock).execute(
        RequestApprovalCommand(
            run_id=run_id,
            category=first.risk.category,
            summary=first.risk.summary,
            reason="writing k",
            requested_action=WRITE.tool,
            requested_input=WRITE.tool_input,
            authorization_fingerprint=first.fingerprint,
        ),
        worker_id=WORKER,
        claim_token=TOKEN,
        claim_generation=_generation(uow_factory, run_id),
    )
    ApproveRequest(uow_factory, clock).execute(
        ApproveRequestCommand(approval_id=approval.approval_id, resolver="patrick")
    )
    generation = _reclaim(uow_factory, run_id)

    other = ToolCall(tool="fixture.write", tool_input={"key": "other", "value": "v"})
    outcome = executor.execute(
        run_id=run_id,
        step_id=None,
        call=other,
        worker_id=WORKER,
        claim_token=TOKEN,
        claim_generation=generation,
    )

    assert outcome.kind == "approval_required"
    assert _remote_value(gateway, "other") is None


def test_a_claim_lost_before_the_call_produces_no_remote_side_effect(
    uow_factory: UnitOfWorkFactory, gateway: McpToolGateway
) -> None:
    """Cancellation is checked by the gateway itself, before the transport."""
    run_id = RunId.new()
    _seed_claimed_run(uow_factory, run_id)
    from friday.application.tool_gateway import ToolExecutionRequest
    from friday.domain.identifiers import ToolInvocationId

    result = gateway.execute(
        ToolExecutionRequest(
            ToolInvocationId.new(),
            run_id,
            None,
            WRITE,
            cancellation_requested=lambda: True,
        )
    )

    assert result.status == "failed"
    assert result.failure is not None and result.failure.code == "claim_lost"
    assert _remote_value(gateway, "k") is None


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "running"])
def test_consumed_v1_approval_replays_only_legacy_local_invocations(
    uow_factory: UnitOfWorkFactory,
    gateway: McpToolGateway,
    clock: FixedClock,
    terminal: str,
) -> None:
    """v1 is replay fencing, never a route into an MCP binding."""
    run_id = RunId.new()
    _seed_claimed_run(uow_factory, run_id)
    approval = _approved_legacy(run_id, WRITE)
    invocation = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=run_id,
        tool_name=WRITE.tool,
        requested_input=WRITE.tool_input,
        requested_at=T0,
        approval_request_id=approval.id,
    )
    invocation.start(T0)
    if terminal == "succeeded":
        invocation.succeed(T0, {"legacy": "result"})
    elif terminal == "failed":
        from friday.domain.failure import Failure, FailureCause

        invocation.fail(T0, Failure("legacy_failed", "legacy failure", False, FailureCause.TOOL))
    with uow_factory() as uow:
        uow.approvals.add(approval)
        uow.tool_invocations.add(invocation)
        uow.commit()

    executor = ExecuteToolAction(uow_factory, clock, gateway)
    if terminal == "running":
        with pytest.raises(ToolExecutionAmbiguous):
            executor.execute(
                run_id=run_id,
                step_id=None,
                call=WRITE,
                worker_id=WORKER,
                claim_token=TOKEN,
                claim_generation=_generation(uow_factory, run_id),
            )
    else:
        outcome = executor.execute(
            run_id=run_id,
            step_id=None,
            call=WRITE,
            worker_id=WORKER,
            claim_token=TOKEN,
            claim_generation=_generation(uow_factory, run_id),
        )
        assert outcome.replayed is True
        assert outcome.result is not None
        assert outcome.result.status == terminal
    assert _remote_value(gateway, "k") is None


def test_unconsumed_v1_and_mcp_provenance_do_not_authorize_or_replay(
    uow_factory: UnitOfWorkFactory, gateway: McpToolGateway, clock: FixedClock
) -> None:
    run_id = RunId.new()
    _seed_claimed_run(uow_factory, run_id)
    legacy = _approved_legacy(run_id, WRITE)
    # v1 approval without consumption cannot authorize a current v2 execution.
    legacy._consumed_at = None  # noqa: SLF001 - explicitly models historic durable row
    with uow_factory() as uow:
        uow.approvals.add(legacy)
        uow.commit()
    assert (
        ExecuteToolAction(uow_factory, clock, gateway)
        .execute(
            run_id=run_id,
            step_id=None,
            call=WRITE,
            worker_id=WORKER,
            claim_token=TOKEN,
            claim_generation=_generation(uow_factory, run_id),
        )
        .kind
        == "approval_required"
    )

    # A consumed v1 record attached to an MCP-provenanced invocation is not
    # local legacy replay and therefore cannot suppress current approval.
    legacy.consume(T0)
    risk = gateway.assess(WRITE)
    invocation = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=run_id,
        tool_name=WRITE.tool,
        requested_input=WRITE.tool_input,
        requested_at=T0,
        approval_request_id=legacy.id,
        provenance=risk.provenance,
    )
    invocation.start(T0)
    invocation.succeed(T0, {"must": "not replay"})
    with uow_factory() as uow:
        uow.approvals.save(legacy)
        uow.tool_invocations.add(invocation)
        uow.commit()
    assert (
        ExecuteToolAction(uow_factory, clock, gateway)
        .execute(
            run_id=run_id,
            step_id=None,
            call=WRITE,
            worker_id=WORKER,
            claim_token=TOKEN,
            claim_generation=_generation(uow_factory, run_id),
        )
        .kind
        == "approval_required"
    )


@pytest.mark.parametrize(
    "behaviour",
    [FixtureBehaviour(write_then_is_error=True), FixtureBehaviour(write_then_hang=True)],
    ids=["protocol-error", "transport-loss"],
)
def test_post_side_effect_mutation_uncertainty_stays_running_and_never_retries(
    uow_factory: UnitOfWorkFactory,
    tmp_path: Path,
    clock: FixedClock,
    behaviour: FixtureBehaviour,
) -> None:
    marker = tmp_path / "writes.txt"
    behaviour = replace(behaviour, write_marker_file=str(marker))
    built = _gateway_with_behaviour(tmp_path, behaviour, call_timeout_seconds=0.1)
    try:
        run_id = RunId.new()
        _seed_claimed_run(uow_factory, run_id)
        executor = ExecuteToolAction(uow_factory, clock, built)
        proposed = executor.execute(
            run_id=run_id,
            step_id=None,
            call=WRITE,
            worker_id=WORKER,
            claim_token=TOKEN,
            claim_generation=_generation(uow_factory, run_id),
        )
        approval = RequestToolApproval(uow_factory, clock).execute(
            RequestApprovalCommand(
                run_id=run_id,
                category=proposed.risk.category,
                summary=proposed.risk.summary,
                reason="write",
                requested_action=WRITE.tool,
                requested_input=WRITE.tool_input,
                authorization_fingerprint=proposed.fingerprint,
            ),
            worker_id=WORKER,
            claim_token=TOKEN,
            claim_generation=_generation(uow_factory, run_id),
        )
        ApproveRequest(uow_factory, clock).execute(
            ApproveRequestCommand(approval_id=approval.approval_id, resolver="operator")
        )
        generation = _reclaim(uow_factory, run_id)
        with pytest.raises(ToolExecutionAmbiguous):
            executor.execute(
                run_id=run_id,
                step_id=None,
                call=WRITE,
                worker_id=WORKER,
                claim_token=TOKEN,
                claim_generation=generation,
            )
        assert marker.read_text(encoding="utf-8") == "k=v\n"
        with uow_factory() as fresh:
            invocations = fresh.tool_invocations.list_for_run(run_id)
            assert len(invocations) == 1 and invocations[0].status is ToolInvocationStatus.RUNNING
            assert invocations[0].approval_request_id == approval.approval_id
            assert fresh.approvals.list_for_run(run_id)[0].is_consumed is True
            assert all(
                event.type.value != "tool_invocation_failed"
                for event in fresh.events.list_for_run(run_id)
            )
        with pytest.raises(ToolExecutionAmbiguous):
            executor.execute(
                run_id=run_id,
                step_id=None,
                call=WRITE,
                worker_id=WORKER,
                claim_token=TOKEN,
                claim_generation=generation,
            )
        assert marker.read_text(encoding="utf-8") == "k=v\n"
    finally:
        built.close()


def test_echoed_credential_is_redacted_from_sqlite_and_next_runtime_context(
    uow_factory: UnitOfWorkFactory, tmp_path: Path, clock: FixedClock
) -> None:
    secret = "credential-only-for-this-e2e-proof"
    built = _gateway_with_behaviour(
        tmp_path,
        FixtureBehaviour(echo_token=True),
        base_environment={"PATH": "/usr/bin:/bin", "FIXTURE_TOKEN": secret},
    )
    try:
        run_id = RunId.new()
        _seed_claimed_run(uow_factory, run_id)
        ExecuteToolAction(uow_factory, clock, built).execute(
            run_id=run_id,
            step_id=None,
            call=READ,
            worker_id=WORKER,
            claim_token=TOKEN,
            claim_generation=_generation(uow_factory, run_id),
        )
        with uow_factory() as fresh:
            invocation = fresh.tool_invocations.list_for_run(run_id)[0]
            events = fresh.events.list_for_run(run_id)
            run = fresh.runs.get(run_id)
            task = fresh.tasks.get(run.task_id) if run is not None else None
            assert run is not None and task is not None
            durable = json.dumps(
                {
                    "output": invocation.output,
                    "provenance": str(invocation.provenance),
                    "events": [e.payload for e in events],
                }
            )
            context = build_runtime_context(
                RunSnapshot(task, run, (), (), (invocation,), (), tuple(events)),
                tool_manifest=built.list_tools(),
                attempt_number=1,
                turn_number=2,
                max_chars=4_000,
            )
        assert secret not in durable
        assert secret not in context
        assert "[REDACTED]" in durable
    finally:
        built.close()
