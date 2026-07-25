from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from friday.application.memory import retrieval
from friday.application.memory.models import (
    IndexState,
    IndexStatus,
    MemoryCandidate,
    MemoryExcerpt,
    MemoryQuery,
    MemoryWriteCandidate,
    MemoryWriteResult,
    RetrievalMethod,
    RetrievalMode,
)
from friday.application.memory.retrieval import MemoryRetrievalSettings, MemoryRetriever


def _query() -> MemoryQuery:
    return MemoryQuery(terms=("plan",))


def _candidate(
    path: str,
    *,
    score: float = 1.0,
    methods: tuple[RetrievalMethod, ...] = (RetrievalMethod.LEXICAL_BODY,),
    distance: int | None = None,
) -> MemoryCandidate:
    return MemoryCandidate(
        path, path, methods, score, graph_distance=distance, index_snapshot_id="index-1"
    )


@dataclass
class FakeStore:
    lexical: tuple[MemoryCandidate, ...] = ()
    texts: dict[str, str] = field(default_factory=dict)
    fail_search: bool = False
    fail_reads: set[str] = field(default_factory=set)
    requested_chars: list[int] = field(default_factory=list)

    def search_lexical(self, query: MemoryQuery, *, limit: int) -> tuple[MemoryCandidate, ...]:
        if self.fail_search:
            raise RuntimeError("unavailable")
        return self.lexical[:limit]

    def read_excerpt(self, candidate: MemoryCandidate, *, max_chars: int) -> MemoryExcerpt:
        self.requested_chars.append(max_chars)
        if candidate.path in self.fail_reads:
            raise OSError("gone")
        text = self.texts[candidate.path][:max_chars]
        return MemoryExcerpt(
            candidate.path,
            f"store {candidate.path}",
            None,
            1,
            1,
            text,
            f"hash-{candidate.path}",
            len(text) < len(self.texts[candidate.path]),
        )

    def write_candidate(self, candidate: MemoryWriteCandidate) -> MemoryWriteResult:
        raise NotImplementedError

    def source_snapshot_hash(self) -> str:
        return "source"


@dataclass
class FakeIndex:
    state: IndexState = IndexState.FRESH
    structural: tuple[MemoryCandidate, ...] = ()
    neighbor_results: dict[str, tuple[MemoryCandidate, ...]] = field(default_factory=dict)
    fail_status: bool = False
    fail_search: bool = False
    calls: list[tuple[str, int, int]] = field(default_factory=list)

    def status(self) -> IndexStatus:
        if self.fail_status:
            raise RuntimeError("status")
        return IndexStatus(self.state, "index-1", None, None, 0, 0, None, None)

    def search(self, query: MemoryQuery, *, limit: int) -> tuple[MemoryCandidate, ...]:
        if self.fail_search:
            raise RuntimeError("search")
        return self.structural[:limit]

    def neighbors(self, path: str, *, depth: int, max_nodes: int) -> tuple[MemoryCandidate, ...]:
        self.calls.append((path, depth, max_nodes))
        return self.neighbor_results.get(path, ())[:max_nodes]


class PromotingSnapshotIndex(FakeIndex):
    """A structural view opened after status() observes a new generation."""

    def retrieve_snapshot(
        self, query: MemoryQuery, *, limit: int, depth: int, max_nodes: int
    ) -> tuple[tuple[MemoryCandidate, ...], str]:
        return self.structural[:limit], "index-2"


def _retriever(store: FakeStore, index: FakeIndex, **settings: int) -> MemoryRetriever:
    return MemoryRetriever(store, index, settings=MemoryRetrievalSettings(**settings))


def test_retrieves_lexical_candidate_with_store_text_and_provenance() -> None:
    store = FakeStore((_candidate("a.md"),), {"a.md": "canonical text"})
    context = _retriever(store, FakeIndex()).retrieve(query=_query())

    assert context.mode is RetrievalMode.HYBRID
    assert context.excerpts[0].text == "canonical text"
    assert context.provenance[0].methods == (RetrievalMethod.LEXICAL_BODY,)
    assert context.provenance[0].rank == 0


def test_uses_authoritative_store_text_for_structural_candidate() -> None:
    structural = _candidate("graph.md", methods=(RetrievalMethod.STRUCTURAL_NODE,), distance=1)
    store = FakeStore(texts={"graph.md": "vault text"})
    context = _retriever(store, FakeIndex(structural=(structural,))).retrieve(query=_query())

    assert context.excerpts[0].text == "vault text"
    assert context.excerpts[0].title == "store graph.md"


def test_hybrid_context_keeps_snapshot_when_top_excerpt_is_lexical() -> None:
    """Audit provenance belongs to the retrieval, not to rank zero.

    A lexical result can rank above a structural result while the structural
    snapshot still influenced the candidate set and ranking.
    """
    lexical = MemoryCandidate("lexical.md", "lexical.md", (RetrievalMethod.LEXICAL_BODY,), 2.0)
    structural = _candidate(
        "structural.md", score=1.0, methods=(RetrievalMethod.STRUCTURAL_NODE,), distance=1
    )
    store = FakeStore((lexical,), {"lexical.md": "lexical", "structural.md": "structural"})

    context = _retriever(store, FakeIndex(structural=(structural,))).retrieve(query=_query())

    assert context.provenance[0].index_snapshot_id is None
    assert context.index_snapshot_id == "index-1"


def test_snapshot_change_during_structural_retrieval_discards_structural_results() -> None:
    lexical = MemoryCandidate("lexical.md", "lexical.md", (RetrievalMethod.LEXICAL_BODY,), 1.0)
    structural = _candidate("structural.md", methods=(RetrievalMethod.STRUCTURAL_NODE,))
    store = FakeStore((lexical,), {"lexical.md": "lexical", "structural.md": "structural"})

    context = _retriever(store, PromotingSnapshotIndex(structural=(structural,))).retrieve(
        query=_query()
    )

    assert context.mode is RetrievalMode.LEXICAL_ONLY
    assert [excerpt.path for excerpt in context.excerpts] == ["lexical.md"]
    assert context.index_snapshot_id is None


def test_merges_duplicate_aliases_methods_and_boosts_rank() -> None:
    lexical = _candidate("notes/./plan.md", score=1.0)
    structural = _candidate(
        "notes/plan.md", score=1.0, methods=(RetrievalMethod.STRUCTURAL_NODE,), distance=1
    )
    other = _candidate("other.md", score=1.5)
    store = FakeStore((lexical, other), {"notes/plan.md": "plan", "other.md": "other"})
    context = _retriever(store, FakeIndex(structural=(structural,))).retrieve(query=_query())

    assert [item.path for item in context.excerpts] == ["notes/plan.md", "other.md"]
    assert context.provenance[0].methods == (
        RetrievalMethod.LEXICAL_BODY,
        RetrievalMethod.STRUCTURAL_NODE,
    )


def test_orders_equal_scores_by_path_deterministically() -> None:
    store = FakeStore((_candidate("z.md"), _candidate("a.md")), {"a.md": "a", "z.md": "z"})
    context = _retriever(store, FakeIndex()).retrieve(query=_query())

    assert [excerpt.path for excerpt in context.excerpts] == ["a.md", "z.md"]


def test_enforces_candidate_excerpt_and_context_bounds() -> None:
    store = FakeStore(
        (_candidate("a.md"), _candidate("b.md"), _candidate("c.md")),
        {"a.md": "aaaa", "b.md": "bbbb", "c.md": "cccc"},
    )
    context = _retriever(
        store,
        FakeIndex(),
        max_candidates=2,
        max_excerpts=2,
        max_excerpt_chars=3,
        max_total_context_chars=6,
        max_graph_depth=1,
        max_graph_nodes_visited=1,
    ).retrieve(query=_query())

    assert [excerpt.path for excerpt in context.excerpts] == ["a.md", "b.md"]
    assert context.total_chars == 6
    assert store.requested_chars == [3, 3]
    assert all(item.truncated for item in context.provenance)


def test_stops_before_an_excerpt_that_exceeds_total_budget() -> None:
    store = FakeStore((_candidate("a.md"),), {"a.md": "four"})
    context = _retriever(
        store,
        FakeIndex(),
        max_candidates=1,
        max_excerpts=1,
        max_excerpt_chars=10,
        max_total_context_chars=3,
        max_graph_depth=1,
        max_graph_nodes_visited=1,
    ).retrieve(query=_query())

    assert context.excerpts == ()
    assert context.provenance == ()


def test_stops_when_excerpt_count_reaches_its_bound() -> None:
    store = FakeStore((_candidate("a.md"), _candidate("b.md")), {"a.md": "a", "b.md": "b"})
    context = _retriever(
        store,
        FakeIndex(),
        max_candidates=2,
        max_excerpts=1,
        max_excerpt_chars=10,
        max_total_context_chars=10,
        max_graph_depth=1,
        max_graph_nodes_visited=1,
    ).retrieve(query=_query())

    assert [excerpt.path for excerpt in context.excerpts] == ["a.md"]


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (IndexState.MISSING, "missing"),
        (IndexState.STALE, "stale"),
        (IndexState.CORRUPT, "rebuild required"),
    ],
)
def test_degrades_to_lexical_only_for_unusable_index(state: IndexState, reason: str) -> None:
    store = FakeStore((_candidate("a.md"),), {"a.md": "a"})
    index = FakeIndex(state=state, structural=(_candidate("graph.md"),))
    context = _retriever(store, index).retrieve(query=_query())

    assert context.mode is RetrievalMode.LEXICAL_ONLY
    assert reason in (context.degraded_reason or "")
    assert [excerpt.path for excerpt in context.excerpts] == ["a.md"]


def test_disabled_index_returns_disabled_mode() -> None:
    store = FakeStore((_candidate("a.md"),), {"a.md": "a"})
    context = _retriever(store, FakeIndex(state=IndexState.DISABLED)).retrieve(query=_query())

    assert context.mode is RetrievalMode.DISABLED
    assert context.degraded_reason == "structural index is disabled"


def test_structural_status_or_search_failures_degrade_without_raising() -> None:
    store = FakeStore((_candidate("a.md"),), {"a.md": "a"})
    status_context = _retriever(store, FakeIndex(fail_status=True)).retrieve(query=_query())
    search_context = _retriever(store, FakeIndex(fail_search=True)).retrieve(query=_query())

    assert status_context.mode is RetrievalMode.LEXICAL_ONLY
    assert search_context.mode is RetrievalMode.LEXICAL_ONLY
    assert "unavailable" in (status_context.degraded_reason or "")
    assert "unavailable" in (search_context.degraded_reason or "")


def test_lexical_failure_returns_empty_unavailable_context() -> None:
    context = _retriever(FakeStore(fail_search=True), FakeIndex()).retrieve(query=_query())

    assert context.mode is RetrievalMode.UNAVAILABLE
    assert context.excerpts == ()
    assert context.total_chars == 0


def test_neighbor_search_honors_depth_and_node_bound() -> None:
    root = _candidate("root.md", methods=(RetrievalMethod.STRUCTURAL_NODE,))
    neighbor = _candidate("neighbor.md", methods=(RetrievalMethod.STRUCTURAL_NEIGHBOR,))
    store = FakeStore(texts={"root.md": "root", "neighbor.md": "neighbor"})
    index = FakeIndex(structural=(root,), neighbor_results={"root.md": (neighbor,)})
    _retriever(
        store,
        index,
        max_candidates=3,
        max_excerpts=3,
        max_excerpt_chars=10,
        max_total_context_chars=20,
        max_graph_depth=2,
        max_graph_nodes_visited=1,
    ).retrieve(query=_query())

    assert index.calls == [("root.md", 2, 1)]


def test_neighbor_search_stops_after_visiting_its_node_bound() -> None:
    first = _candidate("first.md", methods=(RetrievalMethod.STRUCTURAL_NODE,))
    second = _candidate("second.md", methods=(RetrievalMethod.STRUCTURAL_NODE,))
    neighbor = _candidate("neighbor.md", methods=(RetrievalMethod.STRUCTURAL_NEIGHBOR,))
    store = FakeStore(texts={"first.md": "first", "second.md": "second", "neighbor.md": "neighbor"})
    index = FakeIndex(structural=(first, second), neighbor_results={"first.md": (neighbor,)})
    _retriever(
        store,
        index,
        max_candidates=3,
        max_excerpts=3,
        max_excerpt_chars=10,
        max_total_context_chars=20,
        max_graph_depth=1,
        max_graph_nodes_visited=1,
    ).retrieve(query=_query())

    assert index.calls == [("first.md", 1, 1)]


def test_closest_distance_handles_every_optional_distance_combination() -> None:
    assert retrieval._closest_distance(None, 2) == 2
    assert retrieval._closest_distance(2, None) == 2
    assert retrieval._closest_distance(2, 1) == 1


def test_skips_unreadable_excerpt_without_losing_other_provenance() -> None:
    store = FakeStore(
        (_candidate("a.md"), _candidate("b.md")),
        {"a.md": "a", "b.md": "b"},
        fail_reads={"a.md"},
    )
    context = _retriever(store, FakeIndex()).retrieve(query=_query())

    assert [excerpt.path for excerpt in context.excerpts] == ["b.md"]
    assert context.provenance[0].rank == 0


@pytest.mark.parametrize(
    "field",
    [
        "max_candidates",
        "max_excerpts",
        "max_excerpt_chars",
        "max_total_context_chars",
        "max_graph_depth",
        "max_graph_nodes_visited",
    ],
)
def test_settings_reject_non_positive_bounds(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        MemoryRetrievalSettings(**{field: 0})
