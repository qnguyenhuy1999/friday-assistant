"""Real SQLite proofs for delegation provenance and child ownership."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from friday.application.agent_registry import (
    ActivateAgentRevision,
    CreateAgent,
    CreateAgentRevision,
)
from friday.application.brain_runtime_registry import BrainRuntimeRegistry
from friday.application.commands import CreateTaskCommand, StartRunCommand
from friday.application.create_task import CreateTask
from friday.application.delegation import CreateDelegationRequest, DispatchDelegation
from friday.application.ports import UnitOfWorkFactory
from friday.application.start_run import StartRun
from friday.application.worker_coordination import ClaimNextRun
from friday.domain.agent import AgentRevisionSourceKind
from friday.domain.identifiers import RunId, TaskId
from friday.domain.run import Run
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory

REPO_ROOT = Path(__file__).resolve().parents[2]
AT = datetime(2026, 1, 2, 3, tzinfo=UTC)


class Clock:
    def now(self) -> datetime:
        return AT


def _registry() -> BrainRuntimeRegistry:
    registry = BrainRuntimeRegistry()
    registry.register("claude_cli", lambda: None)  # type: ignore[arg-type,return-value]
    return registry


def _migrated_engine(tmp_path: Path) -> Engine:
    db_path = tmp_path / "delegation.db"
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(config, "head")
    return create_engine(f"sqlite:///{db_path}")


def _run(factory: UnitOfWorkFactory, task_id: TaskId, run_id: RunId) -> None:
    run = Run.new(id=run_id, task_id=task_id, created_at=AT)
    with factory() as uow:
        uow.runs.add(run)
        uow.commit()


def test_cross_task_child_run_provenance_is_rejected_by_sqlite(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        factory = create_unit_of_work_factory(create_session_factory(engine))
        clock = Clock()
        parent_task = CreateTask(factory, clock).execute(CreateTaskCommand("parent", ""))
        parent_run_id = RunId.new()
        _run(factory, parent_task.task_id, parent_run_id)

        agent = CreateAgent(factory, clock).execute(
            key="delegation.target", display_name="Target", description=""
        )
        revision = CreateAgentRevision(factory, clock, _registry()).execute(
            agent_id=agent.id,
            instructions="be helpful",
            runtime_kind="claude_cli",
            runtime_config={},
            source_kind=AgentRevisionSourceKind.OPERATOR,
        )
        ActivateAgentRevision(factory, clock).execute(agent_id=agent.id, revision_id=revision.id)
        request = CreateDelegationRequest(factory, clock).execute(
            parent_run_id=parent_run_id,
            target_agent_id=agent.id,
            objective="delegate bounded work",
            input_payload=None,
            expected_output_contract="a result",
        )

        child_task_a = CreateTask(factory, clock).execute(CreateTaskCommand("child-a", ""))
        child_task_b = CreateTask(factory, clock).execute(CreateTaskCommand("child-b", ""))
        child_run_id = RunId.new()
        _run(factory, child_task_b.task_id, child_run_id)

        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE delegation_requests SET child_task_id = :task_id, "
                    "child_run_id = :run_id WHERE id = :delegation_id"
                ),
                {
                    "task_id": str(child_task_a.task_id),
                    "run_id": str(child_run_id),
                    "delegation_id": str(request.id),
                },
            )

        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT child_task_id, child_run_id FROM delegation_requests WHERE id = :id"),
                {"id": str(request.id)},
            ).one()
        assert row.child_task_id is None
        assert row.child_run_id is None
    finally:
        engine.dispose()


def test_real_sqlite_dispatch_materializes_parent_wait_and_child_work(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        factory = create_unit_of_work_factory(create_session_factory(engine))
        clock = Clock()
        parent_task = CreateTask(factory, clock).execute(CreateTaskCommand("parent", "research"))
        start = StartRun(factory, clock).execute(StartRunCommand(parent_task.task_id))
        assert start.run_id is not None
        target = CreateAgent(factory, clock).execute(
            key="delegation.target", display_name="Target", description="evidence"
        )
        revision = CreateAgentRevision(factory, clock, _registry()).execute(
            agent_id=target.id,
            instructions="Gather evidence.",
            runtime_kind="claude_cli",
            runtime_config={},
            source_kind=AgentRevisionSourceKind.OPERATOR,
        )
        ActivateAgentRevision(factory, clock).execute(agent_id=target.id, revision_id=revision.id)
        claim = ClaimNextRun(
            factory,
            clock,
            worker_id="parent-worker",
            lease_duration=timedelta(minutes=1),
            candidate_limit=5,
        ).execute()
        assert claim is not None and claim.run_id == start.run_id

        request = DispatchDelegation(factory, clock, _registry()).execute(
            parent_run_id=claim.run_id,
            worker_id=claim.worker_id,
            claim_token=claim.claim_token,
            claim_generation=claim.claim_generation,
            target_agent_key="delegation.target",
            objective="collect release evidence",
            input_payload={"release": "21.2"},
            expected_output_contract="Return cited evidence.",
        )
        assert request.child_task_id is not None
        assert request.child_run_id is not None
        with factory() as uow:
            parent = uow.runs.get(claim.run_id)
            child = uow.runs.get(request.child_run_id)
            persisted = uow.delegation_requests.get(request.id)
            assert parent is not None and parent.status.value == "waiting_for_delegation"
            assert child is not None and child.execution_id == child.id
            assert persisted is not None and persisted.status.value == "dispatched"
            assert uow.task_agent_bindings.get(request.child_task_id) is not None
            assert uow.work_queue.get(claim.run_id) is None
            assert uow.work_queue.get(request.child_run_id) is not None
            uow.commit()
    finally:
        engine.dispose()


def test_sqlite_fences_duplicate_child_provenance_and_foreign_parent_wait(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        factory = create_unit_of_work_factory(create_session_factory(engine))
        clock = Clock()
        parent_task = CreateTask(factory, clock).execute(CreateTaskCommand("parent", ""))
        parent_run = StartRun(factory, clock).execute(StartRunCommand(parent_task.task_id))
        assert parent_run.run_id is not None
        other_task = CreateTask(factory, clock).execute(CreateTaskCommand("other", ""))
        other_run = StartRun(factory, clock).execute(StartRunCommand(other_task.task_id))
        assert other_run.run_id is not None
        target = CreateAgent(factory, clock).execute(
            key="delegation.target", display_name="Target", description=""
        )
        revision = CreateAgentRevision(factory, clock, _registry()).execute(
            agent_id=target.id,
            instructions="Gather evidence.",
            runtime_kind="claude_cli",
            runtime_config={},
            source_kind=AgentRevisionSourceKind.OPERATOR,
        )
        ActivateAgentRevision(factory, clock).execute(agent_id=target.id, revision_id=revision.id)
        request_a = CreateDelegationRequest(factory, clock).execute(
            parent_run_id=parent_run.run_id,
            target_agent_id=target.id,
            objective="a",
            input_payload={},
            expected_output_contract="result",
        )
        request_b = CreateDelegationRequest(factory, clock).execute(
            parent_run_id=parent_run.run_id,
            target_agent_id=target.id,
            objective="b",
            input_payload={},
            expected_output_contract="result",
        )
        child_task = CreateTask(factory, clock).execute(CreateTaskCommand("child", ""))
        child_run = StartRun(factory, clock).execute(StartRunCommand(child_task.task_id))
        assert child_run.run_id is not None

        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE delegation_requests SET status='dispatched', "
                    "child_task_id=:task_id, child_run_id=:run_id, started_at=:at WHERE id=:id"
                ),
                {
                    "task_id": str(child_task.task_id),
                    "run_id": str(child_run.run_id),
                    "at": AT,
                    "id": str(request_a.id),
                },
            )
            connection.execute(
                text(
                    "UPDATE delegation_requests SET status='dispatched', "
                    "child_task_id=:task_id, child_run_id=:run_id, started_at=:at WHERE id=:id"
                ),
                {
                    "task_id": str(child_task.task_id),
                    "run_id": str(child_run.run_id),
                    "at": AT,
                    "id": str(request_b.id),
                },
            )

        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE runs SET status='waiting_for_delegation', "
                    "delegation_request_id=:request_id WHERE id=:run_id"
                ),
                {"request_id": str(request_a.id), "run_id": str(other_run.run_id)},
            )
    finally:
        engine.dispose()
