"""Deterministic, bounded hybrid retrieval over application memory ports."""

from __future__ import annotations

from dataclasses import dataclass

from friday.application.memory.models import (
    IndexState,
    MemoryCandidate,
    MemoryContext,
    MemoryExcerpt,
    MemoryProvenance,
    MemoryQuery,
    RetrievalMethod,
    RetrievalMode,
)
from friday.application.memory.ports import MemoryRetrieverPort, MemoryStore, StructuralIndex

_DEFAULT_MAX_CANDIDATES = 30
_DEFAULT_MAX_EXCERPTS = 8
_DEFAULT_MAX_EXCERPT_CHARS = 2_000
_DEFAULT_MAX_TOTAL_CONTEXT_CHARS = 8_000
_DEFAULT_MAX_GRAPH_DEPTH = 1
_DEFAULT_MAX_GRAPH_NODES_VISITED = 30
_METHOD_BONUS = 0.1


@dataclass(frozen=True, slots=True)
class MemoryRetrievalSettings:
    """Explicit bounds for one memory retrieval operation."""

    max_candidates: int = _DEFAULT_MAX_CANDIDATES
    max_excerpts: int = _DEFAULT_MAX_EXCERPTS
    max_excerpt_chars: int = _DEFAULT_MAX_EXCERPT_CHARS
    max_total_context_chars: int = _DEFAULT_MAX_TOTAL_CONTEXT_CHARS
    max_graph_depth: int = _DEFAULT_MAX_GRAPH_DEPTH
    max_graph_nodes_visited: int = _DEFAULT_MAX_GRAPH_NODES_VISITED

    def __post_init__(self) -> None:
        for name, value in (
            ("max_candidates", self.max_candidates),
            ("max_excerpts", self.max_excerpts),
            ("max_excerpt_chars", self.max_excerpt_chars),
            ("max_total_context_chars", self.max_total_context_chars),
            ("max_graph_depth", self.max_graph_depth),
            ("max_graph_nodes_visited", self.max_graph_nodes_visited),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True, slots=True)
class _MergedCandidate:
    path: str
    title: str
    headings: tuple[str, ...]
    methods: tuple[RetrievalMethod, ...]
    base_score: float
    score: float
    graph_distance: int | None
    index_snapshot_id: str | None


class MemoryRetriever(MemoryRetrieverPort):
    """Merge lexical and fresh structural candidates into authoritative context.

    The ranking score is ``candidate score + inverse graph distance + method
    bonus``. Inverse graph distance rewards closer graph relationships; the
    small per-method bonus cannot outweigh a material relevance difference.
    Path is the final tie-breaker, making output deterministic.
    """

    def __init__(
        self,
        store: MemoryStore,
        structural_index: StructuralIndex,
        *,
        settings: MemoryRetrievalSettings | None = None,
    ) -> None:
        self._store = store
        self._structural_index = structural_index
        self._settings = settings or MemoryRetrievalSettings()

    def retrieve(self, *, query: MemoryQuery, source_snapshot_hash: str) -> MemoryContext:
        state, degraded_reason = self._index_state()
        try:
            lexical = self._store.search_lexical(query, limit=self._settings.max_candidates)
        except Exception:
            return self._empty_context(
                RetrievalMode.UNAVAILABLE,
                "lexical memory search is unavailable",
                state,
            )

        structural: tuple[MemoryCandidate, ...] = ()
        mode = RetrievalMode.LEXICAL_ONLY
        if state is IndexState.FRESH:
            found = self._structural_candidates(query)
            if found is None:
                degraded_reason = "structural search is unavailable"
            else:
                structural = found
                mode = RetrievalMode.HYBRID
        elif state is IndexState.DISABLED:
            mode = RetrievalMode.DISABLED

        candidates = self._rank((*lexical, *structural))
        return self._build_context(candidates, mode, degraded_reason, state, source_snapshot_hash)

    def _index_state(self) -> tuple[IndexState, str | None]:
        try:
            state = self._structural_index.status().state
        except Exception:
            return IndexState.MISSING, "structural index status is unavailable"
        reasons = {
            IndexState.MISSING: "structural index is missing",
            IndexState.STALE: "structural index is stale",
            IndexState.CORRUPT: "structural index is corrupt; rebuild required",
            IndexState.DISABLED: "structural index is disabled",
        }
        return state, reasons.get(state)

    def _structural_candidates(self, query: MemoryQuery) -> tuple[MemoryCandidate, ...] | None:
        try:
            direct = self._structural_index.search(query, limit=self._settings.max_candidates)
            remaining = self._settings.max_graph_nodes_visited
            neighbors: list[MemoryCandidate] = []
            for candidate in direct:
                if remaining <= 0:
                    break
                found = self._structural_index.neighbors(
                    candidate.path,
                    depth=self._settings.max_graph_depth,
                    max_nodes=remaining,
                )
                neighbors.extend(found[:remaining])
                remaining -= len(found)
            return (*direct, *neighbors)
        except Exception:
            return None

    def _rank(self, candidates: tuple[MemoryCandidate, ...]) -> tuple[_MergedCandidate, ...]:
        merged: dict[str, _MergedCandidate] = {}
        for candidate in candidates:
            path = _canonical_path(candidate.path)
            previous = merged.get(path)
            merged[path] = _merge_candidate(previous, candidate, path)
        ranked = sorted(merged.values(), key=lambda item: (-item.score, item.path))
        return tuple(ranked[: self._settings.max_candidates])

    def _build_context(
        self,
        candidates: tuple[_MergedCandidate, ...],
        mode: RetrievalMode,
        degraded_reason: str | None,
        state: IndexState,
        source_snapshot_hash: str,
    ) -> MemoryContext:
        excerpts: list[MemoryExcerpt] = []
        provenance: list[MemoryProvenance] = []
        total_chars = 0
        for candidate in candidates:
            if len(excerpts) == self._settings.max_excerpts:
                break
            excerpt = self._read_excerpt(candidate)
            if excerpt is None:
                continue
            if total_chars + len(excerpt.text) > self._settings.max_total_context_chars:
                break
            rank = len(excerpts)
            excerpts.append(excerpt)
            provenance.append(
                MemoryProvenance(
                    excerpt.path,
                    excerpt.title,
                    excerpt.heading,
                    excerpt.start_line,
                    excerpt.end_line,
                    excerpt.content_hash,
                    candidate.methods,
                    rank,
                    candidate.index_snapshot_id,
                    source_snapshot_hash,
                    excerpt.truncated,
                )
            )
            total_chars += len(excerpt.text)
        return MemoryContext(
            mode, tuple(excerpts), tuple(provenance), degraded_reason, state, total_chars
        )

    def _read_excerpt(self, candidate: _MergedCandidate) -> MemoryExcerpt | None:
        try:
            return self._store.read_excerpt(
                MemoryCandidate(
                    candidate.path,
                    candidate.title,
                    candidate.methods,
                    candidate.score,
                    candidate.headings,
                    candidate.graph_distance,
                    candidate.index_snapshot_id,
                ),
                max_chars=self._settings.max_excerpt_chars,
            )
        except Exception:
            return None

    def _empty_context(self, mode: RetrievalMode, reason: str, state: IndexState) -> MemoryContext:
        return MemoryContext(mode, (), (), reason, state, 0)


def _canonical_path(path: str) -> str:
    """Normalize aliases without filesystem access or absolute paths."""
    return "/".join(part for part in path.split("/") if part and part != ".")


def _merge_candidate(
    previous: _MergedCandidate | None, candidate: MemoryCandidate, path: str
) -> _MergedCandidate:
    if previous is None:
        methods = tuple(sorted(set(candidate.methods), key=str))
        return _MergedCandidate(
            path,
            candidate.title,
            candidate.headings,
            methods,
            candidate.score,
            _score(candidate.score, candidate.graph_distance, methods),
            candidate.graph_distance,
            candidate.index_snapshot_id,
        )
    methods = tuple(sorted({*previous.methods, *candidate.methods}, key=str))
    distance = _closest_distance(previous.graph_distance, candidate.graph_distance)
    headings = tuple(dict.fromkeys((*previous.headings, *candidate.headings)))
    base_score = max(previous.base_score, candidate.score)
    score = _score(base_score, distance, methods)
    return _MergedCandidate(
        path,
        previous.title,
        headings,
        methods,
        base_score,
        score,
        distance,
        previous.index_snapshot_id or candidate.index_snapshot_id,
    )


def _score(score: float, distance: int | None, methods: tuple[RetrievalMethod, ...]) -> float:
    structural_score = 0.0 if distance is None else 1 / (distance + 1)
    return score + structural_score + _METHOD_BONUS * (len(methods) - 1)


def _closest_distance(first: int | None, second: int | None) -> int | None:
    if first is None:
        return second
    if second is None:
        return first
    return min(first, second)
