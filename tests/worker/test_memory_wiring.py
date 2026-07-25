"""Observable memory wiring at the worker composition root."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from apps.worker import app
from apps.worker.app import create_worker
from apps.worker.preflight import run_memory_preflight
from friday.application.memory.models import IndexState, MemoryQuery, RetrievalMode
from friday.application.ports import UnitOfWorkFactory
from friday.application.run_processor import ProcessingOutcome
from friday.infrastructure.memory.index_metadata import IndexMetadata
from friday.infrastructure.tools.memory_tools import MemoryTools
from tests.worker.fake_claude import make_fake_claude
from tests.worker.test_worker_composition import runtime_settings, worker_settings
from tests.worker.test_worker_loop import _run_and_loop

_FINISH = '{"version": 1, "action": "finish", "result": {"summary": "done"}}'


def _configure_memory(monkeypatch: pytest.MonkeyPatch, vault: Path, index_root: Path) -> None:
    monkeypatch.setenv("FRIDAY_MEMORY_ENABLED", "true")
    monkeypatch.setenv("FRIDAY_OBSIDIAN_VAULT_ROOT", str(vault))
    monkeypatch.setenv("FRIDAY_MEMORY_INCLUDE_GLOBS", "notes/**/*.md")
    monkeypatch.setenv("FRIDAY_GRAPHIFY_INDEX_ROOT", str(index_root))


def _uow_factory(tmp_path: Path) -> UnitOfWorkFactory:
    from friday.infrastructure.persistence.database import create_engine, create_session_factory
    from friday.infrastructure.persistence.models import Base
    from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory

    engine = create_engine(f"sqlite:///{tmp_path / 'memory-stack.db'}")
    Base.metadata.create_all(engine)
    return create_unit_of_work_factory(create_session_factory(engine))


def test_enabled_memory_retrieves_a_fixture_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    notes = vault / "notes" / "project"
    notes.mkdir(parents=True)
    (notes / "release.md").write_text("# Release\nThe yellow canary is ready.", encoding="utf-8")
    _configure_memory(monkeypatch, vault, tmp_path / "index")
    executable, _ = make_fake_claude(tmp_path, action_jsons=[_FINISH])

    worker = create_worker(worker_settings(tmp_path), runtime_settings(tmp_path, executable))
    try:
        retriever = worker.processor._memory_retriever
        assert retriever is not None
        memory = retriever.retrieve(query=MemoryQuery(terms=("canary",)))
        assert memory.mode is RetrievalMode.LEXICAL_ONLY
        assert memory.excerpts[0].path == "notes/project/release.md"
        assert "yellow canary" in memory.excerpts[0].text
    finally:
        worker.engine.dispose()


def test_created_managed_note_is_retrievable_next_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    (vault / "notes").mkdir(parents=True)
    (vault / "notes" / "existing.md").write_text("baseline", encoding="utf-8")
    _configure_memory(monkeypatch, vault, tmp_path / "index")
    monkeypatch.setenv("FRIDAY_OBSIDIAN_MANAGED_ROOT", "AssistantMemory")
    factory = _uow_factory(tmp_path)
    stack = app._memory_stack(factory)
    assert stack.tool_settings is not None
    assert stack.refresh_index is None  # Graphify is intentionally off in this focused flow.
    result = MemoryTools(stack.tool_settings).create_note(
        {
            "path": "AssistantMemory/Inbox/note.md",
            "payload": "managed retrieval canary",
            "memory_category": "explicit_user_request_to_remember",
            "frontmatter": {
                "friday_managed": "true",
                "friday_memory_id": "m1",
                "source_run_id": "r1",
                "created_at": "now",
                "updated_at": "now",
            },
        }
    )
    assert result.status == "succeeded"

    memory = stack.retriever.retrieve(query=MemoryQuery(terms=("canary",)))
    assert [excerpt.path for excerpt in memory.excerpts] == ["AssistantMemory/Inbox/note.md"]


def test_disabled_memory_does_not_construct_a_vault_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FRIDAY_MEMORY_ENABLED", "false")

    def fail_if_constructed(*args: object, **kwargs: object) -> object:
        raise AssertionError("disabled memory must not enumerate or construct the vault store")

    monkeypatch.setattr(app, "ObsidianVaultStore", fail_if_constructed)
    executable, _ = make_fake_claude(tmp_path, action_jsons=[_FINISH])
    worker = create_worker(worker_settings(tmp_path), runtime_settings(tmp_path, executable))
    try:
        retriever = worker.processor._memory_retriever
        assert retriever is not None
        memory = retriever.retrieve(query=MemoryQuery(terms=("never",)))
        assert memory.mode is RetrievalMode.DISABLED
    finally:
        worker.engine.dispose()


def test_enabled_memory_without_include_globs_is_disabled_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FRIDAY_MEMORY_ENABLED", "true")
    monkeypatch.delenv("FRIDAY_MEMORY_INCLUDE_GLOBS", raising=False)
    monkeypatch.setenv("FRIDAY_OBSIDIAN_VAULT_ROOT", str(tmp_path / "vault"))

    report = run_memory_preflight()
    assert report.ok is False
    assert report.checks[0][2] == "configuration missing"

    stack = app._memory_stack(_uow_factory(tmp_path))
    memory = stack.retriever.retrieve(query=MemoryQuery(terms=("never",)))
    assert memory.mode is RetrievalMode.DISABLED


def test_elapsed_memory_maintenance_interval_refreshes_once() -> None:
    _, _, loop, _ = _run_and_loop(ProcessingOutcome.succeeded())

    class Refresh:
        calls = 0

        def execute(self) -> None:
            self.calls += 1

    refresh = Refresh()
    loop._refresh_memory_index = refresh
    loop._memory_index_maintenance_interval_seconds = 1.0
    loop._last_memory_index_maintenance = time.monotonic() - 2.0
    loop._refresh_memory_index_if_due()
    loop._refresh_memory_index_if_due()

    assert refresh.calls == 1


def test_graphify_disabled_ignores_existing_fresh_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale/fresh graph.json left on disk from a previous run must never
    influence retrieval once Graphify is disabled -- the flag guarantees
    zero structural influence, not just zero rebuilds."""
    vault = tmp_path / "vault"
    notes = vault / "notes" / "project"
    notes.mkdir(parents=True)
    (notes / "release.md").write_text("# Release\nThe yellow canary is ready.", encoding="utf-8")
    index_root = tmp_path / "index"

    graphify = tmp_path / "graphify"
    graphify.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = --version ]; then echo fake; exit 0; fi\n'
        "out=\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = --out ]; then out="$2"; break; fi\n'
        "  shift\n"
        "done\n"
        'mkdir -p "$out/graphify-out"\n'
        "printf '%s\\n' "
        '\'{"directed":false,"multigraph":false,"graph":{},"nodes":[],'
        '"links":[],"hyperedges":[],"built_at_commit":"x"}\' '
        '> "$out/graphify-out/graph.json"\n',
        encoding="utf-8",
    )
    graphify.chmod(graphify.stat().st_mode | 0o111)

    monkeypatch.setenv("FRIDAY_MEMORY_ENABLED", "true")
    monkeypatch.setenv("FRIDAY_OBSIDIAN_VAULT_ROOT", str(vault))
    monkeypatch.setenv("FRIDAY_MEMORY_INCLUDE_GLOBS", "notes/**/*.md")
    monkeypatch.setenv("FRIDAY_GRAPHIFY_INDEX_ROOT", str(index_root))
    monkeypatch.setenv("FRIDAY_GRAPHIFY_ENABLED", "true")
    monkeypatch.setenv("FRIDAY_GRAPHIFY_EXECUTABLE", str(graphify))

    enabled_stack = app._memory_stack(_uow_factory(tmp_path))
    assert enabled_stack.refresh_index is not None
    snapshot = enabled_stack.refresh_index.execute()
    assert snapshot is not None
    assert snapshot.state is IndexState.FRESH

    monkeypatch.setenv("FRIDAY_GRAPHIFY_ENABLED", "false")
    disabled_stack = app._memory_stack(_uow_factory(tmp_path))
    assert disabled_stack.refresh_index is None

    memory = disabled_stack.retriever.retrieve(query=MemoryQuery(terms=("canary",)))
    assert memory.mode is RetrievalMode.LEXICAL_ONLY


def test_real_worker_registers_memory_read_tools_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    (vault / "notes" / "project").mkdir(parents=True)
    (vault / "notes" / "project" / "release.md").write_text("release note", encoding="utf-8")
    _configure_memory(monkeypatch, vault, tmp_path / "index")
    executable, _ = make_fake_claude(tmp_path, action_jsons=[_FINISH])

    worker = create_worker(worker_settings(tmp_path), runtime_settings(tmp_path, executable))
    try:
        names = {tool.name for tool in worker.processor._gateway.list_tools()}
        assert "memory.search" in names
        assert "memory.read_note" in names
    finally:
        worker.engine.dispose()


def test_real_worker_registers_memory_write_tools_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    (vault / "notes" / "project").mkdir(parents=True)
    (vault / "notes" / "project" / "release.md").write_text("release note", encoding="utf-8")
    _configure_memory(monkeypatch, vault, tmp_path / "index")
    executable, _ = make_fake_claude(tmp_path, action_jsons=[_FINISH])

    worker = create_worker(worker_settings(tmp_path), runtime_settings(tmp_path, executable))
    try:
        names = {tool.name for tool in worker.processor._gateway.list_tools()}
        assert "memory.create_note" in names
        assert "memory.append_managed_note" in names
    finally:
        worker.engine.dispose()


def test_real_worker_does_not_register_memory_tools_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FRIDAY_MEMORY_ENABLED", "false")
    executable, _ = make_fake_claude(tmp_path, action_jsons=[_FINISH])

    worker = create_worker(worker_settings(tmp_path), runtime_settings(tmp_path, executable))
    try:
        names = {tool.name for tool in worker.processor._gateway.list_tools()}
        assert not any(name.startswith("memory.") for name in names)
    finally:
        worker.engine.dispose()


def test_real_worker_memory_create_flows_through_approval_and_tool_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: memory.create_note must go through the same exact-action
    approval + durable ToolInvocation pipeline as any other mutating tool --
    it must not be an implementation detail that only exists in unit tests."""
    import json
    from datetime import UTC, datetime

    from alembic import command
    from alembic.config import Config

    from friday.application.approval_workflow import ApproveRequest
    from friday.application.commands import ApproveRequestCommand
    from friday.domain.identifiers import RunId, TaskId
    from friday.domain.run import Run, RunStatus
    from friday.domain.task import Task
    from friday.domain.tool import ToolInvocationStatus
    from friday.infrastructure.clock import SystemClock
    from friday.infrastructure.persistence.database import create_session_factory
    from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    create_note = json.dumps(
        {
            "version": 1,
            "action": "invoke_tool",
            "tool": "memory.create_note",
            "input": {
                "path": "Friday/Inbox/note.md",
                "payload": "remember to check release status weekly",
                "frontmatter": {
                    "friday_managed": "true",
                    "friday_memory_id": "mem-001",
                    "source_run_id": "run-001",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                },
                "memory_category": "explicit_decision",
            },
        }
    )

    vault = tmp_path / "vault"
    (vault / "notes" / "project").mkdir(parents=True)
    (vault / "notes" / "project" / "release.md").write_text("release note", encoding="utf-8")
    _configure_memory(monkeypatch, vault, tmp_path / "index")
    executable, _ = make_fake_claude(tmp_path, action_jsons=[create_note, create_note, _FINISH])

    worker_config = worker_settings(tmp_path)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", worker_config.database_url)
    command.upgrade(config, "head")

    worker = create_worker(worker_config, runtime_settings(tmp_path, executable))
    try:
        factory = create_unit_of_work_factory(create_session_factory(worker.engine))
        task = Task.new(id=TaskId.new(), title="t", description="remember something", created_at=t0)
        task.start(t0)
        with factory() as uow:
            uow.tasks.add(task)
            uow.commit()
        run = Run.new(id=RunId.new(), task_id=task.id, created_at=t0)
        with factory() as uow:
            uow.runs.add(run)
            uow.work_queue.enqueue(run.id, available_at=t0, enqueued_at=t0)
            uow.commit()

        assert worker.loop.run_once(worker.processor) is True
        with factory() as uow:
            reloaded = uow.runs.get(run.id)
            assert reloaded is not None
            assert reloaded.status is RunStatus.WAITING_FOR_APPROVAL
            approvals = uow.approvals.list_for_run(run.id)
            assert len(approvals) == 1
            approval_id = approvals[0].id
            assert approvals[0].authorization_fingerprint is not None

        ApproveRequest(factory, SystemClock()).execute(
            ApproveRequestCommand(approval_id=approval_id, resolver="patrick")
        )

        assert worker.loop.run_once(worker.processor) is True
        with factory() as uow:
            reloaded = uow.runs.get(run.id)
            assert reloaded is not None
            assert reloaded.status is RunStatus.SUCCEEDED
            invocations = uow.tool_invocations.list_for_run(run.id)
            assert len(invocations) == 1
            assert invocations[0].status is ToolInvocationStatus.SUCCEEDED
            approvals = uow.approvals.list_for_run(run.id)
            assert approvals[0].is_consumed is True

        written = vault / "Friday" / "Inbox" / "note.md"
        assert "remember to check release status weekly" in written.read_text(encoding="utf-8")
    finally:
        worker.engine.dispose()


def test_index_build_persists_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """BuildMemoryIndex must durably persist the IndexSnapshot it returns --
    not just hand it back to an in-memory caller."""
    vault = tmp_path / "vault"
    notes = vault / "notes" / "project"
    notes.mkdir(parents=True)
    (notes / "release.md").write_text("release note", encoding="utf-8")
    index_root = tmp_path / "index"

    graphify = tmp_path / "graphify"
    graphify.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = --version ]; then echo fake; exit 0; fi\n'
        "out=\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = --out ]; then out="$2"; break; fi\n'
        "  shift\n"
        "done\n"
        'mkdir -p "$out/graphify-out"\n'
        "printf '%s\\n' "
        '\'{"directed":false,"multigraph":false,"graph":{},"nodes":[],'
        '"links":[],"hyperedges":[],"built_at_commit":"x"}\' '
        '> "$out/graphify-out/graph.json"\n',
        encoding="utf-8",
    )
    graphify.chmod(graphify.stat().st_mode | 0o111)

    monkeypatch.setenv("FRIDAY_MEMORY_ENABLED", "true")
    monkeypatch.setenv("FRIDAY_OBSIDIAN_VAULT_ROOT", str(vault))
    monkeypatch.setenv("FRIDAY_MEMORY_INCLUDE_GLOBS", "notes/**/*.md")
    monkeypatch.setenv("FRIDAY_GRAPHIFY_INDEX_ROOT", str(index_root))
    monkeypatch.setenv("FRIDAY_GRAPHIFY_ENABLED", "true")
    monkeypatch.setenv("FRIDAY_GRAPHIFY_EXECUTABLE", str(graphify))

    factory = _uow_factory(tmp_path)
    stack = app._memory_stack(factory)
    assert stack.refresh_index is not None
    snapshot = stack.refresh_index.execute()
    assert snapshot is not None

    # A brand new UnitOfWork (fresh session) must still see it: proof it was
    # committed, not merely returned to the in-memory caller.
    with factory() as uow:
        persisted = uow.memory_index_snapshots.latest()
    assert persisted is not None
    assert persisted.id == snapshot.id
    assert persisted.state is snapshot.state
    active = index_root / snapshot.vault_identity_hash[:32] / "active"
    assert IndexMetadata.read(active).snapshot_id == persisted.id


def test_retrieval_audit_persists_from_real_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real turn's memory retrieval must leave a durable
    MemoryRetrievalRecord (with items) behind -- not just an in-memory
    MemoryContext handed to the brain."""
    from datetime import UTC, datetime

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from friday.domain.identifiers import RunId, TaskId
    from friday.domain.run import Run
    from friday.domain.task import Task
    from friday.infrastructure.persistence.database import create_session_factory
    from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    vault = tmp_path / "vault"
    (vault / "notes" / "project").mkdir(parents=True)
    (vault / "notes" / "project" / "release.md").write_text(
        "# Release\nThe yellow canary is ready.", encoding="utf-8"
    )
    _configure_memory(monkeypatch, vault, tmp_path / "index")
    executable, _ = make_fake_claude(tmp_path, action_jsons=[_FINISH])

    worker_config = worker_settings(tmp_path)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", worker_config.database_url)
    command.upgrade(config, "head")

    worker = create_worker(worker_config, runtime_settings(tmp_path, executable))
    try:
        factory = create_unit_of_work_factory(create_session_factory(worker.engine))
        task = Task.new(
            id=TaskId.new(), title="t", description="find the canary release note", created_at=t0
        )
        task.start(t0)
        with factory() as uow:
            uow.tasks.add(task)
            uow.commit()
        run = Run.new(id=RunId.new(), task_id=task.id, created_at=t0)
        with factory() as uow:
            uow.runs.add(run)
            uow.work_queue.enqueue(run.id, available_at=t0, enqueued_at=t0)
            uow.commit()

        assert worker.loop.run_once(worker.processor) is True

        with worker.engine.connect() as connection:
            records = connection.execute(
                text(
                    "SELECT id, run_id, candidate_count FROM memory_retrieval_records"
                    " WHERE run_id = :run_id"
                ),
                {"run_id": str(run.id)},
            ).all()
            assert len(records) == 1
            items = connection.execute(
                text("SELECT path FROM memory_retrieval_items WHERE record_id = :record_id"),
                {"record_id": records[0].id},
            ).all()
            assert [row.path for row in items] == ["notes/project/release.md"]
    finally:
        worker.engine.dispose()


def test_retrieval_audit_source_snapshot_matches_vault_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The persisted audit's source_snapshot_id must be the vault's own
    curated snapshot hash -- never the query hash or any other caller
    supplied value."""
    from datetime import UTC, datetime

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from friday.application.memory.models import MemoryVaultPolicy
    from friday.domain.identifiers import RunId, TaskId
    from friday.domain.run import Run
    from friday.domain.task import Task
    from friday.infrastructure.memory.obsidian_vault import ObsidianVaultStore
    from friday.infrastructure.persistence.database import create_session_factory
    from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    vault_path = tmp_path / "vault"
    (vault_path / "notes" / "project").mkdir(parents=True)
    (vault_path / "notes" / "project" / "release.md").write_text(
        "# Release\nThe yellow canary is ready.", encoding="utf-8"
    )
    _configure_memory(monkeypatch, vault_path, tmp_path / "index")
    executable, _ = make_fake_claude(tmp_path, action_jsons=[_FINISH])

    worker_config = worker_settings(tmp_path)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", worker_config.database_url)
    command.upgrade(config, "head")

    worker = create_worker(worker_config, runtime_settings(tmp_path, executable))
    try:
        factory = create_unit_of_work_factory(create_session_factory(worker.engine))
        task = Task.new(
            id=TaskId.new(), title="t", description="find the canary release note", created_at=t0
        )
        task.start(t0)
        with factory() as uow:
            uow.tasks.add(task)
            uow.commit()
        run = Run.new(id=RunId.new(), task_id=task.id, created_at=t0)
        with factory() as uow:
            uow.runs.add(run)
            uow.work_queue.enqueue(run.id, available_at=t0, enqueued_at=t0)
            uow.commit()

        assert worker.loop.run_once(worker.processor) is True

        expected_hash = ObsidianVaultStore(
            vault_path, MemoryVaultPolicy(("notes/**/*.md",), (), 2000, 200_000)
        ).source_snapshot_hash()

        with worker.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT source_snapshot_id FROM memory_retrieval_records WHERE run_id = :run_id"
                ),
                {"run_id": str(run.id)},
            ).one()
            assert row.source_snapshot_id == expected_hash
            assert row.source_snapshot_id != MemoryQuery(terms=("canary",)).query_hash
    finally:
        worker.engine.dispose()


def test_memory_context_event_is_committed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MEMORY_CONTEXT_ATTACHED must survive being read back from a brand new
    UnitOfWork -- proof _record_memory_events actually commits."""
    from datetime import UTC, datetime

    from alembic import command
    from alembic.config import Config

    from friday.domain.event import RunEventType
    from friday.domain.identifiers import RunId, TaskId
    from friday.domain.run import Run
    from friday.domain.task import Task
    from friday.infrastructure.persistence.database import create_session_factory
    from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    vault = tmp_path / "vault"
    (vault / "notes" / "project").mkdir(parents=True)
    (vault / "notes" / "project" / "release.md").write_text("release note", encoding="utf-8")
    _configure_memory(monkeypatch, vault, tmp_path / "index")
    executable, _ = make_fake_claude(tmp_path, action_jsons=[_FINISH])

    worker_config = worker_settings(tmp_path)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", worker_config.database_url)
    command.upgrade(config, "head")

    worker = create_worker(worker_config, runtime_settings(tmp_path, executable))
    try:
        factory = create_unit_of_work_factory(create_session_factory(worker.engine))
        task = Task.new(id=TaskId.new(), title="t", description="remember something", created_at=t0)
        task.start(t0)
        with factory() as uow:
            uow.tasks.add(task)
            uow.commit()
        run = Run.new(id=RunId.new(), task_id=task.id, created_at=t0)
        with factory() as uow:
            uow.runs.add(run)
            uow.work_queue.enqueue(run.id, available_at=t0, enqueued_at=t0)
            uow.commit()

        assert worker.loop.run_once(worker.processor) is True

        with factory() as uow:
            events = uow.events.list_for_run(run.id)
        kinds = {event.type for event in events}
        assert RunEventType.MEMORY_CONTEXT_ATTACHED in kinds
    finally:
        worker.engine.dispose()
