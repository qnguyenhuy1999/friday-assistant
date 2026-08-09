"""Real SQLite proofs for Phase 21 Step 1 persistence invariants: Agent
revision ownership, immutability, claim-fenced Run Agent resolution, retry
inheritance, and Task-binding scoping — mirroring
tests/persistence/test_skill_registry_sqlite.py's proof style exactly.
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
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
    ReplaceTaskAgent,
    ResolveRunAgent,
)
from friday.application.brain_runtime_registry import BrainRuntimeRegistry
from friday.application.commands import (
    CreateTaskCommand,
    FailRunCommand,
    RetryFailedRunCommand,
    StartQueuedRunCommand,
    StartRunCommand,
)
from friday.application.create_task import CreateTask
from friday.application.errors import (
    AgentIntegrityFailed,
    ClaimLost,
    InvalidBrainRuntimeConfig,
    UnknownBrainRuntimeKind,
)
from friday.application.lifecycle import FailRun, RetryFailedRun, StartQueuedRun
from friday.application.ports import UnitOfWorkFactory
from friday.application.results import RunClaimResult
from friday.application.start_run import StartRun
from friday.application.worker_coordination import ClaimNextRun
from friday.domain.agent import AgentRevisionSourceKind, AgentStatus, RunAgentResolution
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import AgentRevisionId, RunId
from friday.domain.run import Run
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory

REPO_ROOT = Path(__file__).resolve().parents[2]
AT = "2026-01-02 03:00:00"
FAILURE = Failure("x", "failed", True, FailureCause.RUNTIME)


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 1, 2, 3, tzinfo=UTC)


def _registry() -> BrainRuntimeRegistry:
    registry = BrainRuntimeRegistry()
    registry.register("claude_cli", lambda: None)  # type: ignore[arg-type,return-value]
    return registry


def _migrated_engine(tmp_path: Path) -> Engine:
    db_path = tmp_path / "agent-registry.db"
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(config, "head")
    return create_engine(f"sqlite:///{db_path}")


def _insert_agent(engine: Engine, agent_id: str, key: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO agents VALUES (:id, :key, 'n', '', 'active', NULL, :at, :at)"),
            {"id": agent_id, "key": key, "at": AT},
        )


def _insert_revision(engine: Engine, revision_id: str, agent_id: str, version: int) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO agent_revisions "
                "(id, agent_id, version, instructions, runtime_kind, runtime_config, "
                "content_sha256, source_kind, created_at) "
                "VALUES (:id, :agent_id, :version, :instructions, 'claude_cli', '{}', "
                ":sha, 'operator', :at)"
            ),
            {
                "id": revision_id,
                "agent_id": agent_id,
                "version": version,
                "instructions": f"revision {version}",
                "sha": "a" * 64,
                "at": AT,
            },
        )


def _try_set_active(engine: Engine, agent_id: str, revision_id: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE agents SET active_revision_id = :revision_id WHERE id = :agent_id"),
            {"agent_id": agent_id, "revision_id": revision_id},
        )


def _revision_digest(instructions: str, runtime_kind: str, runtime_config: object) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "instructions": instructions,
                "runtime_kind": runtime_kind,
                "runtime_config": runtime_config,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _active_revision_id(factory: UnitOfWorkFactory, run_id: RunId) -> AgentRevisionId:
    with factory() as uow:
        run = uow.runs.get(run_id)
        assert run is not None
        binding = uow.task_agent_bindings.get(run.task_id)
        assert binding is not None
        agent = uow.agents.get(binding.agent_id)
        assert agent is not None and agent.active_revision_id is not None
        return agent.active_revision_id


def test_active_pointer_to_nonexistent_revision_is_rejected(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        _insert_agent(engine, "00000000-0000-0000-0000-000000000001", "fence.missing")
        with pytest.raises(IntegrityError):
            _try_set_active(
                engine,
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-0000000000ff",
            )
    finally:
        engine.dispose()


def test_active_pointer_to_another_agents_revision_is_rejected(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        _insert_agent(engine, "00000000-0000-0000-0000-000000000001", "fence.a")
        _insert_agent(engine, "00000000-0000-0000-0000-000000000002", "fence.b")
        _insert_revision(
            engine,
            "00000000-0000-0000-0000-000000000003",
            "00000000-0000-0000-0000-000000000002",
            1,
        )
        with pytest.raises(IntegrityError):
            _try_set_active(
                engine,
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000003",
            )
    finally:
        engine.dispose()


def test_active_pointer_to_own_revision_succeeds(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        _insert_agent(engine, "00000000-0000-0000-0000-000000000001", "fence.valid")
        _insert_revision(
            engine,
            "00000000-0000-0000-0000-000000000002",
            "00000000-0000-0000-0000-000000000001",
            1,
        )
        _try_set_active(
            engine,
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
        )
        with engine.connect() as connection:
            active = connection.execute(
                text("SELECT active_revision_id FROM agents WHERE id = :agent_id"),
                {"agent_id": "00000000-0000-0000-0000-000000000001"},
            ).scalar_one()
        assert active == "00000000-0000-0000-0000-000000000002"
    finally:
        engine.dispose()


def test_persisted_v1_is_immutable_across_v2_and_activation(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        factory = create_unit_of_work_factory(create_session_factory(engine))
        clock = Clock()
        registry = _registry()
        agent = CreateAgent(factory, clock).execute(
            key="fence.immutable", display_name="I", description=""
        )
        v1 = CreateAgentRevision(factory, clock, registry).execute(
            agent_id=agent.id,
            instructions="original v1 content",
            runtime_kind="claude_cli",
            runtime_config={},
            source_kind=AgentRevisionSourceKind.OPERATOR,
        )
        v2 = CreateAgentRevision(factory, clock, registry).execute(
            agent_id=agent.id,
            instructions="replacement v2 content",
            runtime_kind="claude_cli",
            runtime_config={},
            source_kind=AgentRevisionSourceKind.OPERATOR,
        )
        ActivateAgentRevision(factory, clock).execute(agent_id=agent.id, revision_id=v2.id)

        with factory() as uow:
            reloaded_v1 = uow.agent_revisions.get(v1.id)
            reloaded_agent = uow.agents.get(agent.id)
            assert reloaded_v1 is not None
            assert reloaded_agent is not None
            assert reloaded_v1.instructions == "original v1 content"
            assert reloaded_v1.version == 1
            assert reloaded_agent.active_revision_id == v2.id
            assert reloaded_agent.status is AgentStatus.ACTIVE
    finally:
        engine.dispose()


def _claimed_run(
    engine: Engine, clock: Clock, *, with_agent: bool
) -> tuple[UnitOfWorkFactory, RunId, RunClaimResult]:
    """Seed an Alembic-created database and return one exact active claim."""
    factory = create_unit_of_work_factory(create_session_factory(engine))
    task_id = CreateTask(factory, clock).execute(CreateTaskCommand("Resolve", "")).task_id
    if with_agent:
        registry = _registry()
        agent = CreateAgent(factory, clock).execute(
            key="fence.runtime", display_name="Runtime", description=""
        )
        revision = CreateAgentRevision(factory, clock, registry).execute(
            agent_id=agent.id,
            instructions="Use the frozen instruction.",
            runtime_kind="claude_cli",
            runtime_config={},
            source_kind=AgentRevisionSourceKind.OPERATOR,
        )
        ActivateAgentRevision(factory, clock).execute(agent_id=agent.id, revision_id=revision.id)
        ReplaceTaskAgent(factory, clock).execute(task_id=task_id, agent_id=agent.id)
    # StartRun (not a raw Run.new()+add) so the owning Task transitions to
    # ACTIVE — RetryFailedRun requires an ACTIVE task, and the raw-Run
    # helper the Skill sqlite tests use never needs a retry.
    run_id = StartRun(factory, clock).execute(StartRunCommand(task_id)).run_id
    assert run_id is not None
    claim = ClaimNextRun(
        factory,
        clock,
        worker_id="resolver-a",
        lease_duration=timedelta(minutes=5),
        candidate_limit=4,
    ).execute()
    assert claim is not None
    return factory, run_id, claim


def test_real_sqlite_concurrent_resolution_converges_to_one_freeze(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        clock = Clock()
        factory, run_id, claim = _claimed_run(engine, clock, with_agent=True)

        def resolve() -> RunAgentResolution | None:
            return ResolveRunAgent(factory, clock, _registry()).execute(
                run_id, claim.worker_id, claim.claim_token, claim.claim_generation
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _index: resolve(), range(2)))
        agent_ids = {result.agent_id for result in results if result is not None}
        assert len(agent_ids) == 1
        with factory() as uow:
            assert uow.run_agent_resolutions.get(run_id) is not None
    finally:
        engine.dispose()


def test_real_sqlite_stale_claim_cannot_freeze_agent_resolution(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        clock = Clock()
        factory, run_id, claim = _claimed_run(engine, clock, with_agent=True)
        with pytest.raises(ClaimLost, match="exact active worker claim"):
            ResolveRunAgent(factory, clock, _registry()).execute(
                run_id, claim.worker_id, "wrong-token", claim.claim_generation
            )
        with factory() as uow:
            assert uow.run_agent_resolutions.get(run_id) is None
    finally:
        engine.dispose()


def test_real_sqlite_corrupted_active_revision_fails_before_marker(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        clock = Clock()
        factory, run_id, claim = _claimed_run(engine, clock, with_agent=True)
        revision_id = _active_revision_id(factory, run_id)
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE agent_revisions SET instructions = 'corrupted' WHERE id = :id"),
                {"id": str(revision_id)},
            )

        with pytest.raises(AgentIntegrityFailed):
            ResolveRunAgent(factory, clock, _registry()).execute(
                run_id, claim.worker_id, claim.claim_token, claim.claim_generation
            )
        with factory() as uow:
            assert uow.run_agent_resolutions.get(run_id) is None
    finally:
        engine.dispose()


def test_real_sqlite_unknown_active_runtime_fails_before_marker(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        clock = Clock()
        factory, run_id, claim = _claimed_run(engine, clock, with_agent=True)
        revision_id = _active_revision_id(factory, run_id)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE agent_revisions SET runtime_kind = :kind, content_sha256 = :sha "
                    "WHERE id = :id"
                ),
                {
                    "id": str(revision_id),
                    "kind": "unsupported_runtime",
                    "sha": _revision_digest(
                        "Use the frozen instruction.", "unsupported_runtime", {}
                    ),
                },
            )

        with pytest.raises(UnknownBrainRuntimeKind):
            ResolveRunAgent(factory, clock, _registry()).execute(
                run_id, claim.worker_id, claim.claim_token, claim.claim_generation
            )
        with factory() as uow:
            assert uow.run_agent_resolutions.get(run_id) is None
    finally:
        engine.dispose()


def test_real_sqlite_invalid_persisted_runtime_config_fails_before_marker(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        clock = Clock()
        factory, run_id, claim = _claimed_run(engine, clock, with_agent=True)
        revision_id = _active_revision_id(factory, run_id)
        invalid_config = {"command": "unsafe"}
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE agent_revisions SET runtime_config = :config, content_sha256 = :sha "
                    "WHERE id = :id"
                ),
                {
                    "id": str(revision_id),
                    "config": json.dumps(invalid_config, separators=(",", ":")),
                    "sha": _revision_digest(
                        "Use the frozen instruction.", "claude_cli", invalid_config
                    ),
                },
            )

        with pytest.raises(InvalidBrainRuntimeConfig):
            ResolveRunAgent(factory, clock, _registry()).execute(
                run_id, claim.worker_id, claim.claim_token, claim.claim_generation
            )
        with factory() as uow:
            assert uow.run_agent_resolutions.get(run_id) is None
    finally:
        engine.dispose()


def test_real_sqlite_valid_claude_revision_resolves_normally(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        clock = Clock()
        factory, run_id, claim = _claimed_run(engine, clock, with_agent=True)
        resolution = ResolveRunAgent(factory, clock, _registry()).execute(
            run_id, claim.worker_id, claim.claim_token, claim.claim_generation
        )
        assert resolution is not None
        with factory() as uow:
            assert uow.run_agent_resolutions.get(run_id) == resolution
    finally:
        engine.dispose()


def test_real_sqlite_agentless_run_remains_unresolved(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        clock = Clock()
        factory, run_id, claim = _claimed_run(engine, clock, with_agent=False)
        assert (
            ResolveRunAgent(factory, clock, _registry()).execute(
                run_id, claim.worker_id, claim.claim_token, claim.claim_generation
            )
            is None
        )
        with factory() as uow:
            assert uow.run_agent_resolutions.get(run_id) is None
    finally:
        engine.dispose()


def test_real_sqlite_run_without_queue_row_fails_closed_with_claim_lost(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        clock = Clock()
        factory = create_unit_of_work_factory(create_session_factory(engine))
        task_id = CreateTask(factory, clock).execute(CreateTaskCommand("NoQueue", "")).task_id
        run = Run.new(id=RunId.new(), task_id=task_id, created_at=clock.now())
        with factory() as uow:
            uow.runs.add(run)
            uow.commit()
        with pytest.raises(ClaimLost, match="exact active worker claim"):
            ResolveRunAgent(factory, clock, _registry()).execute(run.id, "resolver-a", "token", 1)
        with factory() as uow:
            assert uow.run_agent_resolutions.get(run.id) is None
    finally:
        engine.dispose()


def test_real_sqlite_wrong_owner_token_or_generation_is_rejected(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        clock = Clock()
        factory, run_id, claim = _claimed_run(engine, clock, with_agent=True)
        attempts = [
            ("intruder", claim.claim_token, claim.claim_generation),
            (claim.worker_id, "wrong-token", claim.claim_generation),
            (claim.worker_id, claim.claim_token, claim.claim_generation + 1),
        ]
        for worker_id, claim_token, claim_generation in attempts:
            with pytest.raises(ClaimLost, match="exact active worker claim"):
                ResolveRunAgent(factory, clock, _registry()).execute(
                    run_id, worker_id, claim_token, claim_generation
                )
        with factory() as uow:
            assert uow.run_agent_resolutions.get(run_id) is None
    finally:
        engine.dispose()


def test_real_sqlite_retry_copies_only_exact_source_resolution(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        clock = Clock()
        factory, run_id, claim = _claimed_run(engine, clock, with_agent=True)
        resolution = ResolveRunAgent(factory, clock, _registry()).execute(
            run_id, claim.worker_id, claim.claim_token, claim.claim_generation
        )
        assert resolution is not None
        StartQueuedRun(factory, clock).execute(StartQueuedRunCommand(run_id))
        FailRun(factory, clock).execute(FailRunCommand(run_id, FAILURE))
        retry = RetryFailedRun(factory, clock).execute(RetryFailedRunCommand(run_id))

        with factory() as uow:
            copied = uow.run_agent_resolutions.get(retry.run_id)
            assert copied is not None
            assert copied.agent_id == resolution.agent_id
            assert copied.revision_id == resolution.revision_id
            assert copied.id != resolution.id
    finally:
        engine.dispose()


def test_real_sqlite_already_resolved_run_unaffected_by_later_activation(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        clock = Clock()
        factory, run_id, claim = _claimed_run(engine, clock, with_agent=True)
        resolution = ResolveRunAgent(factory, clock, _registry()).execute(
            run_id, claim.worker_id, claim.claim_token, claim.claim_generation
        )
        assert resolution is not None

        registry = _registry()
        new_agent = CreateAgent(factory, clock).execute(
            key="fence.later", display_name="Later", description=""
        )
        new_revision = CreateAgentRevision(factory, clock, registry).execute(
            agent_id=new_agent.id,
            instructions="a newer instruction",
            runtime_kind="claude_cli",
            runtime_config={},
            source_kind=AgentRevisionSourceKind.OPERATOR,
        )
        ActivateAgentRevision(factory, clock).execute(
            agent_id=new_agent.id, revision_id=new_revision.id
        )

        with factory() as uow:
            unchanged = uow.run_agent_resolutions.get(run_id)
            assert unchanged is not None
            assert unchanged.agent_id == resolution.agent_id
            assert unchanged.revision_id == resolution.revision_id
    finally:
        engine.dispose()


def test_real_sqlite_task_binding_change_only_affects_future_unresolved_runs(
    tmp_path: Path,
) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        clock = Clock()
        factory, run_id, claim = _claimed_run(engine, clock, with_agent=True)
        resolution = ResolveRunAgent(factory, clock, _registry()).execute(
            run_id, claim.worker_id, claim.claim_token, claim.claim_generation
        )
        assert resolution is not None

        with factory() as uow:
            run = uow.runs.get(run_id)
            assert run is not None
            task_id = run.task_id

        registry = _registry()
        other_agent = CreateAgent(factory, clock).execute(
            key="fence.other", display_name="Other", description=""
        )
        other_revision = CreateAgentRevision(factory, clock, registry).execute(
            agent_id=other_agent.id,
            instructions="different agent instruction",
            runtime_kind="claude_cli",
            runtime_config={},
            source_kind=AgentRevisionSourceKind.OPERATOR,
        )
        ActivateAgentRevision(factory, clock).execute(
            agent_id=other_agent.id, revision_id=other_revision.id
        )
        ReplaceTaskAgent(factory, clock).execute(task_id=task_id, agent_id=other_agent.id)

        with factory() as uow:
            unchanged = uow.run_agent_resolutions.get(run_id)
            assert unchanged is not None
            assert unchanged.agent_id == resolution.agent_id
    finally:
        engine.dispose()


def test_real_sqlite_historical_run_stays_unresolved_after_migration(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        clock = Clock()
        factory = create_unit_of_work_factory(create_session_factory(engine))
        task_id = CreateTask(factory, clock).execute(CreateTaskCommand("Historical", "")).task_id
        run = Run.new(id=RunId.new(), task_id=task_id, created_at=clock.now())
        with factory() as uow:
            uow.runs.add(run)
            uow.commit()
        with factory() as uow:
            assert uow.run_agent_resolutions.get(run.id) is None
    finally:
        engine.dispose()
