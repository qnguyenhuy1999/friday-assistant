"""Worker composition (25.10): fail-closed construction, real processor
injection, and end-to-end Run progression — finish, approval interception,
resume-after-approval, and approved tool execution against a real SQLite
database, the real gateway, and a fake Claude executable."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from apps.worker.app import Worker, create_worker
from apps.worker.runtime_settings import RuntimeSettings
from apps.worker.settings import WorkerSettings
from friday.application.agent_run_processor import AgentRunProcessor
from friday.application.approval_workflow import ApproveRequest
from friday.application.commands import ApproveRequestCommand
from friday.application.errors import BrainUnavailable, WorkspaceAccessDenied
from friday.domain.identifiers import RunId, TaskId
from friday.domain.run import Run, RunStatus
from friday.domain.task import Task
from friday.domain.tool import ToolInvocationStatus
from friday.infrastructure.clock import SystemClock
from friday.infrastructure.persistence.database import create_session_factory
from friday.infrastructure.persistence.models import Base
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory
from tests.worker.fake_claude import make_fake_claude

T0 = datetime(2026, 1, 1, tzinfo=UTC)
FINISH = '{"version": 1, "action": "finish", "result": {"summary": "done"}}'
WRITE = json.dumps(
    {
        "version": 1,
        "action": "invoke_tool",
        "tool": "workspace.write_text",
        "input": {"path": "out.txt", "content": "hello"},
    }
)


def worker_settings(tmp_path: Path) -> WorkerSettings:
    return WorkerSettings(
        database_url=f"sqlite:///{tmp_path / 'worker.db'}",
        worker_id="test-worker",
        lease_duration=timedelta(seconds=60),
        candidate_limit=10,
        poll_interval_seconds=0.01,
        heartbeat_interval_seconds=0.05,
        maintenance_interval_seconds=0.05,
        maintenance_batch_size=100,
        retry_max_attempts=3,
        retry_base_delay=timedelta(seconds=5),
        retry_multiplier=2.0,
        retry_max_delay=timedelta(seconds=300),
    )


def runtime_settings(
    tmp_path: Path, executable: str, workspace: Path | None = None
) -> RuntimeSettings:
    if workspace is None:
        workspace = tmp_path / "workspace"
        workspace.mkdir(exist_ok=True)
    return RuntimeSettings(
        workspace_root=workspace,
        brain_backend="claude_cli",
        claude_executable=executable,
        claude_model=None,
        claude_timeout_seconds=30.0,
        claude_max_output_bytes=1_000_000,
        max_turns_per_claim=8,
        max_tool_calls_per_claim=4,
        max_context_chars=60_000,
        max_response_bytes=65_536,
        max_yield_seconds=3_600,
        tool_timeout_seconds=10.0,
        tool_max_timeout_seconds=30.0,
        tool_max_stdout_bytes=100_000,
        tool_max_stderr_bytes=100_000,
        tool_max_file_bytes=1_000_000,
        tool_max_list_entries=100,
    )


def build_worker(tmp_path: Path, action_jsons: list[str]) -> Worker:
    executable, _ = make_fake_claude(tmp_path, action_jsons=action_jsons)
    worker = create_worker(worker_settings(tmp_path), runtime_settings(tmp_path, executable))
    Base.metadata.create_all(worker.engine)
    return worker


def seed_queued_run(worker: Worker) -> RunId:
    factory = create_unit_of_work_factory(create_session_factory(worker.engine))
    task = Task.new(id=TaskId.new(), title="t", description="write out.txt", created_at=T0)
    task.start(T0)
    with factory() as uow:
        uow.tasks.add(task)
        uow.commit()
    run = Run.new(id=RunId.new(), task_id=task.id, created_at=T0)
    with factory() as uow:
        uow.runs.add(run)
        uow.work_queue.enqueue(run.id, available_at=T0, enqueued_at=T0)
        uow.commit()
    return run.id


# --- fail-closed construction -------------------------------------------------


def test_missing_executable_prevents_construction(tmp_path: Path) -> None:
    with pytest.raises(BrainUnavailable):
        create_worker(
            worker_settings(tmp_path),
            runtime_settings(tmp_path, str(tmp_path / "no-such-claude")),
        )


def test_cli_without_brain_only_flags_prevents_construction(tmp_path: Path) -> None:
    executable, _ = make_fake_claude(
        tmp_path, action_jsons=[FINISH], flags=("--print", "--output-format")
    )
    with pytest.raises(BrainUnavailable) as excinfo:
        create_worker(worker_settings(tmp_path), runtime_settings(tmp_path, executable))
    assert "--tools" in str(excinfo.value)


def test_invalid_workspace_prevents_construction(tmp_path: Path) -> None:
    executable, _ = make_fake_claude(tmp_path, action_jsons=[FINISH])
    runtime = runtime_settings(tmp_path, executable, workspace=tmp_path / "missing-workspace")
    with pytest.raises(WorkspaceAccessDenied):
        create_worker(worker_settings(tmp_path), runtime)


def test_construction_injects_a_real_processor(tmp_path: Path) -> None:
    worker = build_worker(tmp_path, [FINISH])
    try:
        assert isinstance(worker.processor, AgentRunProcessor)
    finally:
        worker.engine.dispose()


# --- end-to-end progression ----------------------------------------------------


def test_run_progresses_to_succeeded_through_brain_finish(tmp_path: Path) -> None:
    worker = build_worker(tmp_path, [FINISH])
    try:
        run_id = seed_queued_run(worker)
        assert worker.loop.run_once(worker.processor) is True
        factory = create_unit_of_work_factory(create_session_factory(worker.engine))
        with factory() as uow:
            run = uow.runs.get(run_id)
            assert run is not None
            assert run.status is RunStatus.SUCCEEDED
            assert uow.work_queue.get(run_id) is None
    finally:
        worker.engine.dispose()


def test_full_approval_cycle_executes_the_approved_tool(tmp_path: Path) -> None:
    # claim 1: brain proposes a protected write -> approval intercepted
    # human approves -> run resumes with a fresh work item
    # claim 2: brain proposes the same write (authorized now) then finishes
    worker = build_worker(tmp_path, [WRITE, WRITE, FINISH])
    try:
        run_id = seed_queued_run(worker)
        factory = create_unit_of_work_factory(create_session_factory(worker.engine))

        assert worker.loop.run_once(worker.processor) is True
        with factory() as uow:
            run = uow.runs.get(run_id)
            assert run is not None
            assert run.status is RunStatus.WAITING_FOR_APPROVAL
            approvals = uow.approvals.list_for_run(run_id)
            assert len(approvals) == 1
            approval_id = approvals[0].id
            assert approvals[0].authorization_fingerprint is not None
            assert uow.work_queue.get(run_id) is None  # parked

        ApproveRequest(factory, SystemClock()).execute(
            ApproveRequestCommand(approval_id=approval_id, resolver="patrick")
        )
        with factory() as uow:
            run = uow.runs.get(run_id)
            assert run is not None
            assert run.status is RunStatus.RUNNING
            assert uow.work_queue.get(run_id) is not None  # fresh work item

        assert worker.loop.run_once(worker.processor) is True
        with factory() as uow:
            run = uow.runs.get(run_id)
            assert run is not None
            assert run.status is RunStatus.SUCCEEDED
            invocations = uow.tool_invocations.list_for_run(run_id)
            assert len(invocations) == 1
            assert invocations[0].status is ToolInvocationStatus.SUCCEEDED
            approvals = uow.approvals.list_for_run(run_id)
            assert approvals[0].is_consumed is True
            artifacts = uow.artifacts.list_for_run(run_id)
            assert len(artifacts) == 1
            assert artifacts[0].location == "out.txt"

        # the tool actually wrote the file inside the confined workspace
        written = tmp_path / "workspace" / "out.txt"
        assert written.read_text() == "hello"
    finally:
        worker.engine.dispose()


def test_worker_check_preflight_passes_on_healthy_environment(tmp_path: Path) -> None:
    from alembic import command
    from alembic.config import Config

    from apps.worker.preflight import run_preflight

    executable, _ = make_fake_claude(tmp_path, action_jsons=[FINISH])
    settings = worker_settings(tmp_path)
    runtime = runtime_settings(tmp_path, executable)

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")

    report = run_preflight(settings, runtime)
    assert report.ok, report.checks


def test_preflight_reports_problems_without_claiming(tmp_path: Path) -> None:
    from apps.worker.preflight import run_preflight

    settings = worker_settings(tmp_path)  # DB exists but has no alembic_version
    runtime = runtime_settings(tmp_path, str(tmp_path / "no-such-claude"))
    report = run_preflight(settings, runtime)
    assert not report.ok
    failed = {name for name, passed, _ in report.checks if not passed}
    assert "claude_brain_only" in failed
    assert "migration_head" in failed
    # preflight never creates, enqueues, or claims anything
    from friday.infrastructure.persistence.database import create_engine

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            tables = connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).all()
        assert ("run_work_items",) not in tables
    finally:
        engine.dispose()
