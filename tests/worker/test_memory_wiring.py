"""Observable memory wiring at the worker composition root."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from apps.worker import app
from apps.worker.app import create_worker
from apps.worker.preflight import run_memory_preflight
from friday.application.memory.models import MemoryQuery, RetrievalMode
from friday.application.run_processor import ProcessingOutcome
from tests.worker.fake_claude import make_fake_claude
from tests.worker.test_worker_composition import runtime_settings, worker_settings
from tests.worker.test_worker_loop import _run_and_loop

_FINISH = '{"version": 1, "action": "finish", "result": {"summary": "done"}}'


def _configure_memory(monkeypatch: pytest.MonkeyPatch, vault: Path, index_root: Path) -> None:
    monkeypatch.setenv("FRIDAY_MEMORY_ENABLED", "true")
    monkeypatch.setenv("FRIDAY_OBSIDIAN_VAULT_ROOT", str(vault))
    monkeypatch.setenv("FRIDAY_MEMORY_INCLUDE_GLOBS", "notes/**/*.md")
    monkeypatch.setenv("FRIDAY_GRAPHIFY_INDEX_ROOT", str(index_root))


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
        memory = retriever.retrieve(
            query=MemoryQuery(terms=("canary",)), source_snapshot_hash="test"
        )
        assert memory.mode is RetrievalMode.LEXICAL_ONLY
        assert memory.excerpts[0].path == "notes/project/release.md"
        assert "yellow canary" in memory.excerpts[0].text
    finally:
        worker.engine.dispose()


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
        memory = retriever.retrieve(
            query=MemoryQuery(terms=("never",)), source_snapshot_hash="test"
        )
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

    stack = app._memory_stack()
    memory = stack.retriever.retrieve(
        query=MemoryQuery(terms=("never",)), source_snapshot_hash="test"
    )
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
