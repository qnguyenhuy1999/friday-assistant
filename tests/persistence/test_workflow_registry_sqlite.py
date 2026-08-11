from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from friday.application.agent_registry import CreateAgent
from friday.application.errors import WorkflowIntegrityFailed
from friday.application.ports import UnitOfWorkFactory
from friday.application.workflow_registry import (
    CreateWorkflow,
    CreateWorkflowRevision,
)
from friday.domain import (
    AgentId,
    WorkflowEdgeId,
    WorkflowId,
    WorkflowNode,
    WorkflowNodeId,
    WorkflowRevision,
    WorkflowRevisionId,
    WorkflowRevisionSourceKind,
)
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.repositories import WorkflowRevisionRepository
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory

REPO_ROOT = Path(__file__).resolve().parents[2]
AT = datetime(2026, 1, 2, 3, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return AT


def _config(db_path: Path) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def _migrated_engine(tmp_path: Path) -> Engine:
    db_path = tmp_path / "workflow.db"
    command.upgrade(_config(db_path), "head")
    return create_engine(f"sqlite:///{db_path}")


def _seed_registry(
    engine: Engine,
) -> tuple[UnitOfWorkFactory, WorkflowId, AgentId, WorkflowRevision]:
    factory = create_unit_of_work_factory(create_session_factory(engine))
    clock = _Clock()
    agent = CreateAgent(factory, clock).execute(
        key="workflow.target",
        display_name="Workflow target",
        description="",
    )
    workflow = CreateWorkflow(factory, clock).execute(
        key="workflow.registry",
        display_name="Workflow registry",
        description="",
    )

    revision = CreateWorkflowRevision(factory, clock).execute(
        workflow_id=workflow.id,
        nodes=[
            {
                "node_key": "a",
                "target_agent_id": str(agent.id),
                "objective": "first",
                "input_payload": {},
                "expected_output_contract": "done",
            },
            {
                "node_key": "b",
                "target_agent_id": str(agent.id),
                "objective": "second",
                "input_payload": {},
                "expected_output_contract": "done",
            },
        ],
        edges=[{"from_node": "a", "to_node": "b"}],
        source_kind=WorkflowRevisionSourceKind.OPERATOR,
    )
    return factory, workflow.id, agent.id, revision


def test_sqlite_workflow_keyset_pagination_tie_breaks_by_id(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        factory, first_id, _, _ = _seed_registry(engine)
        second = CreateWorkflow(factory, _Clock()).execute(
            key="workflow.tie-breaker",
            display_name="Tie breaker",
            description="",
        )
        assert second.created_at == AT

        with factory() as uow:
            first_page = uow.workflows.list_page(1, None, None)
            after_page = uow.workflows.list_page(1, first_page[0].created_at, str(first_page[0].id))

        expected_ids = sorted((str(first_id), str(second.id)))
        assert [str(value.id) for value in first_page] == expected_ids[:1]
        assert [str(value.id) for value in after_page] == expected_ids[1:]
    finally:
        engine.dispose()


@pytest.mark.parametrize("mutation", ["node", "edge", "sha"])
def test_raw_sql_workflow_corruption_fails_load_as_integrity_error(
    tmp_path: Path, mutation: str
) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        factory, _, _, revision = _seed_registry(engine)
        node_a, node_b = revision.nodes
        with engine.begin() as connection:
            if mutation == "node":
                connection.execute(
                    text("UPDATE workflow_nodes SET objective = 'tampered' WHERE id = :id"),
                    {"id": str(node_a.id)},
                )
            elif mutation == "edge":
                connection.execute(
                    text(
                        "UPDATE workflow_edges SET from_node_id = :from_id, "
                        "to_node_id = :to_id WHERE revision_id = :revision_id"
                    ),
                    {
                        "from_id": str(node_b.id),
                        "to_id": str(node_a.id),
                        "revision_id": str(revision.id),
                    },
                )
            else:
                connection.execute(
                    text("UPDATE workflow_revisions SET content_sha256 = :sha WHERE id = :id"),
                    {"sha": "0" * 64, "id": str(revision.id)},
                )

        with pytest.raises(WorkflowIntegrityFailed), factory() as uow:
            uow.workflow_revisions.get(revision.id)
    finally:
        engine.dispose()


@pytest.mark.parametrize("key", ["", "k" * 129])
def test_raw_sql_workflow_key_length_constraint_rejects_invalid_keys(
    tmp_path: Path, key: str
) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO workflows "
                    "(id, key, display_name, description, status, active_revision_id, "
                    "created_at, updated_at) VALUES "
                    "(:id, :key, 'name', '', 'active', NULL, :at, :at)"
                ),
                {"id": str(WorkflowId.new()), "key": key, "at": AT},
            )
    finally:
        engine.dispose()


def test_sqlite_rejects_cross_revision_edge_ownership(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        factory, workflow_id, agent_id, first = _seed_registry(engine)
        second_workflow = CreateWorkflow(factory, _Clock()).execute(
            key="workflow.other",
            display_name="Other workflow",
            description="",
        )
        second = CreateWorkflowRevision(factory, _Clock()).execute(
            workflow_id=second_workflow.id,
            nodes=[
                {
                    "node_key": "other-a",
                    "target_agent_id": str(agent_id),
                    "objective": "other",
                    "input_payload": {},
                    "expected_output_contract": "done",
                },
                {
                    "node_key": "other-b",
                    "target_agent_id": str(agent_id),
                    "objective": "other",
                    "input_payload": {},
                    "expected_output_contract": "done",
                },
            ],
            edges=[{"from_node": "other-a", "to_node": "other-b"}],
            source_kind=WorkflowRevisionSourceKind.OPERATOR,
        )
        assert workflow_id != second_workflow.id

        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO workflow_edges "
                    "(id, revision_id, from_node_id, to_node_id, created_at) "
                    "VALUES (:id, :revision_id, :from_node_id, :to_node_id, :at)"
                ),
                {
                    "id": str(WorkflowEdgeId.new()),
                    "revision_id": str(first.id),
                    "from_node_id": str(second.nodes[0].id),
                    "to_node_id": str(first.nodes[1].id),
                    "at": AT,
                },
            )
    finally:
        engine.dispose()


def test_sqlite_rejects_foreign_active_workflow_revision(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        factory, first_workflow_id, agent_id, _ = _seed_registry(engine)
        second_workflow = CreateWorkflow(factory, _Clock()).execute(
            key="workflow.other",
            display_name="Other workflow",
            description="",
        )
        second = CreateWorkflowRevision(factory, _Clock()).execute(
            workflow_id=second_workflow.id,
            nodes=[
                {
                    "node_key": "only",
                    "target_agent_id": str(agent_id),
                    "objective": "other",
                    "input_payload": {},
                    "expected_output_contract": "done",
                }
            ],
            edges=[],
            source_kind=WorkflowRevisionSourceKind.OPERATOR,
        )

        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE workflows SET active_revision_id = :revision_id WHERE id = :workflow_id"
                ),
                {"revision_id": str(second.id), "workflow_id": str(first_workflow_id)},
            )
    finally:
        engine.dispose()


def _concurrent_revision(workflow_id: WorkflowId, agent_id: AgentId, key: str) -> WorkflowRevision:
    revision_id = WorkflowRevisionId.new()
    node_id = WorkflowNodeId.new()
    node = WorkflowNode(
        id=node_id,
        revision_id=revision_id,
        node_key=key,
        target_agent_id=agent_id,
        objective=key,
        input_payload={},
        expected_output_contract="done",
        created_at=AT,
    )
    return WorkflowRevision.new(
        id=revision_id,
        workflow_id=workflow_id,
        version=2,
        nodes=[node],
        edges=[],
        source_kind=WorkflowRevisionSourceKind.OPERATOR,
        created_at=AT,
    )


def test_concurrent_workflow_revision_writers_fail_closed_and_preserve_order(
    tmp_path: Path,
) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        factory, workflow_id, agent_id, first = _seed_registry(engine)
        session_factory = create_session_factory(engine)
        revisions = [
            _concurrent_revision(workflow_id, agent_id, "concurrent-a"),
            _concurrent_revision(workflow_id, agent_id, "concurrent-b"),
        ]
        barrier = Barrier(2)

        def write(revision: WorkflowRevision) -> str:
            session: Session = session_factory()
            try:
                repository = WorkflowRevisionRepository(session)
                assert repository.next_version(workflow_id) == 2
                session.rollback()
                barrier.wait(timeout=5)
                repository.add(revision)
                session.commit()
                return "committed"
            except IntegrityError:
                session.rollback()
                return "integrity_error"
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(write, revisions))

        assert sorted(outcomes) == ["committed", "integrity_error"]
        with factory() as uow:
            persisted = uow.workflow_revisions.list_for_workflow(workflow_id)
            assert [revision.version for revision in persisted] == [1, 2]

            third = _concurrent_revision(workflow_id, agent_id, "third")
            third = WorkflowRevision(
                id=third.id,
                workflow_id=third.workflow_id,
                version=uow.workflow_revisions.next_version(workflow_id),
                content_sha256=third.content_sha256,
                source_kind=third.source_kind,
                nodes=third.nodes,
                edges=third.edges,
                created_at=third.created_at,
            )
            uow.workflow_revisions.add(third)
            uow.commit()

        with factory() as uow:
            versions = [
                revision.version
                for revision in uow.workflow_revisions.list_for_workflow(workflow_id)
            ]
            assert versions == [1, 2, 3]
        assert first.version == 1
    finally:
        engine.dispose()
