"""Memory retrieval integration for the bounded agent processor."""

from __future__ import annotations

from typing import cast

import pytest

from friday.application.agent_run_processor import RuntimeLimits, _bounded_read
from friday.application.brain_runtime import BrainResponse
from friday.application.memory.models import IndexState, MemoryContext, MemoryQuery, RetrievalMode
from friday.application.memory.query_builder import MemoryQueryBuilder
from friday.application.ports import UnitOfWorkFactory
from friday.application.runtime_actions import FinishAction, InvokeToolAction
from tests.application import test_agent_run_processor as _processor_regression_tests
from tests.application.test_agent_run_processor import FINISH, READ, Harness

# Keep the targeted coverage gate representative of the complete processor
# contract while this module adds its memory-specific cases.
for _name in dir(_processor_regression_tests):
    if _name.startswith("test_"):
        globals()[_name] = getattr(_processor_regression_tests, _name)


class RecordingRetriever:
    def __init__(self, harness: Harness, *, lose_claim: bool = False, fail: bool = False) -> None:
        self.harness = harness
        self.lose_claim = lose_claim
        self.fail = fail
        self.calls: list[MemoryQuery] = []

    def retrieve(self, *, query: MemoryQuery, source_snapshot_hash: str) -> MemoryContext:
        self.calls.append(query)
        assert source_snapshot_hash == query.query_hash
        if self.lose_claim:
            self.harness.uow.work_queue_repo.remove(self.harness.run.id)
        if self.fail:
            raise RuntimeError("vault unavailable")
        return MemoryContext(RetrievalMode.LEXICAL_ONLY, (), (), None, IndexState.MISSING, 0)


class OrderingUnitOfWorkFactory:
    def __init__(self, factory: object, events: list[str]) -> None:
        self._factory = factory
        self._events = events
        self.open_count = 0

    def __call__(self) -> OrderingUnitOfWorkFactory:
        self._uow = self._factory()  # type: ignore[operator]
        return self

    def __enter__(self) -> object:
        self.open_count += 1
        self._events.append("uow_opened")
        return self._uow.__enter__()

    def __exit__(self, *args: object) -> object:
        result = self._uow.__exit__(*args)
        self.open_count -= 1
        self._events.append("uow_closed")
        return result


class OrderingRetriever(RecordingRetriever):
    def __init__(
        self, harness: Harness, events: list[str], factory: OrderingUnitOfWorkFactory
    ) -> None:
        super().__init__(harness)
        self._events = events
        self._factory = factory

    def retrieve(self, *, query: MemoryQuery, source_snapshot_hash: str) -> MemoryContext:
        assert self._factory.open_count == 0
        self._events.append("retrieve")
        return super().retrieve(query=query, source_snapshot_hash=source_snapshot_hash)


def _with_memory(harness: Harness, retriever: RecordingRetriever) -> None:
    harness.processor._memory_retriever = retriever


def test_retrieves_memory_before_the_first_brain_turn_and_reuses_it() -> None:
    harness = Harness(READ, FINISH)
    retriever = RecordingRetriever(harness)
    _with_memory(harness, retriever)

    outcome = harness.processor.process(harness.context())

    assert outcome.kind == "succeeded"
    assert len(retriever.calls) == 1
    assert len(harness.brain.requests) == 2
    assert "# MEMORY" in harness.brain.requests[0].context


def test_retrieval_runs_only_after_the_snapshot_unit_of_work_has_closed() -> None:
    harness = Harness(FINISH)
    events: list[str] = []
    factory = OrderingUnitOfWorkFactory(harness.factory, events)
    harness.processor._uow_factory = cast(UnitOfWorkFactory, factory)
    retriever = OrderingRetriever(harness, events, factory)
    _with_memory(harness, retriever)

    outcome = harness.processor.process(harness.context())

    assert outcome.kind == "succeeded"
    assert events.index("uow_closed") < events.index("retrieve")
    assert factory.open_count == 0


def test_claim_loss_before_retrieval_skips_retrieval_and_brain() -> None:
    harness = Harness(FINISH)
    retriever = RecordingRetriever(harness)
    _with_memory(harness, retriever)
    harness.uow.work_queue_repo.remove(harness.run.id)

    outcome = harness.processor.process(harness.context())

    assert outcome.kind == "yielded"
    assert retriever.calls == []
    assert harness.brain.requests == []


def test_claim_loss_during_retrieval_discards_memory_and_skips_brain() -> None:
    harness = Harness(FINISH)
    retriever = RecordingRetriever(harness, lose_claim=True)
    _with_memory(harness, retriever)

    outcome = harness.processor.process(harness.context())

    assert outcome.kind == "yielded"
    assert len(retriever.calls) == 1
    assert harness.brain.requests == []


def test_claim_loss_after_retrieval_discards_memory_without_recording_events() -> None:
    harness = Harness(FINISH)
    retriever = RecordingRetriever(harness, lose_claim=True)
    _with_memory(harness, retriever)

    outcome = harness.processor.process(harness.context())

    assert outcome.kind == "yielded"
    assert len(retriever.calls) == 1
    assert all("# MEMORY" not in request.context for request in harness.brain.requests)
    assert harness.uow.event_store.appended == []


def test_retrieval_failure_uses_safe_marker_and_continues() -> None:
    harness = Harness(FinishAction(summary="done"))
    retriever = RecordingRetriever(harness, fail=True)
    _with_memory(harness, retriever)

    outcome = harness.processor.process(harness.context())

    assert outcome.kind == "succeeded"
    assert "# MEMORY\nmemory unavailable" in harness.brain.requests[0].context


def test_successful_memory_write_triggers_only_one_bounded_refresh() -> None:
    append_memory = InvokeToolAction("memory.append", {}, None)
    harness = Harness(append_memory, READ, READ, FINISH)
    retriever = RecordingRetriever(harness)
    _with_memory(harness, retriever)

    outcome = harness.processor.process(harness.context())

    assert outcome.kind == "succeeded"
    assert len(harness.brain.requests) == 4
    assert len(retriever.calls) == 2
    assert all("# MEMORY" in request.context for request in harness.brain.requests)


def test_empty_memory_query_skips_retriever_and_uses_disabled_context() -> None:
    harness = Harness(FINISH)
    retriever = RecordingRetriever(harness)

    class EmptyQueryBuilder:
        def build(self, snapshot: object) -> None:
            return None

    _with_memory(harness, retriever)
    harness.processor._memory_query_builder = cast(MemoryQueryBuilder, EmptyQueryBuilder())
    outcome = harness.processor.process(harness.context())

    assert outcome.kind == "succeeded"
    assert retriever.calls == []
    assert "# MEMORY" in harness.brain.requests[0].context


def test_memory_write_detection_only_accepts_memory_tools() -> None:
    assert (
        Harness(FINISH).processor._is_successful_memory_write(
            BrainResponse(action=FinishAction(summary="done"))
        )
        is False
    )
    from friday.application.runtime_actions import InvokeToolAction

    assert (
        Harness(FINISH).processor._is_successful_memory_write(
            BrainResponse(action=InvokeToolAction("memory.append", {}, None))
        )
        is True
    )


def test_processor_yields_when_the_claim_deadline_is_already_expired() -> None:
    harness = Harness(FINISH)
    ticks = iter((0.0, 1_000.0))
    harness.processor._monotonic = lambda: next(ticks)

    assert harness.processor.process(harness.context()).kind == "yielded"


def test_processing_limit_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_processing_seconds"):
        RuntimeLimits(1, 1, 1_000, 1, 0, 0)


def test_memory_event_recording_tolerates_a_missing_run() -> None:
    harness = Harness(FINISH)
    harness.uow.run_repo.items.clear()
    harness.processor._record_memory_events(
        harness.context(),
        MemoryContext(RetrievalMode.DISABLED, (), (), None, IndexState.DISABLED, 0),
    )
    assert harness.uow.event_store.appended == []


def test_bounded_read_prefers_the_repository_bounded_query() -> None:
    class RecentRepository:
        def list_recent_for_run(self, run_id: object, limit: int) -> tuple[str, ...]:
            return ("recent",)

    assert _bounded_read(RecentRepository(), object(), 1) == ("recent",)


@pytest.mark.parametrize("ticks", ((0.0, 0.0, 1_000.0), (0.0, 0.0, 0.0, 1_000.0)))
def test_processor_yields_at_deadline_checkpoints(ticks: tuple[float, ...]) -> None:
    harness = Harness(FINISH)
    values = iter(ticks)
    harness.processor._monotonic = lambda: next(values)

    assert harness.processor.process(harness.context()).kind == "yielded"
