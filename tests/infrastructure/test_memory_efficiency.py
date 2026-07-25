"""Bounded retrieval fixtures: verify work counts rather than elapsed time."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from friday.application.memory.models import (
    IndexState,
    IndexStatus,
    MemoryCandidate,
    MemoryExcerpt,
    MemoryQuery,
    MemoryVaultPolicy,
    MemoryWriteCandidate,
    MemoryWriteResult,
    RetrievalMethod,
)
from friday.application.memory.retrieval import MemoryRetrievalSettings, MemoryRetriever
from friday.infrastructure.memory.lexical_index import LexicalIndexStore


def _policy(max_files: int = 2_000) -> MemoryVaultPolicy:
    return MemoryVaultPolicy(("notes/*.md",), (), max_files, 200_000)


def _write_notes(root: Path, count: int, *, body: str = "body") -> None:
    notes = root / "notes"
    notes.mkdir(parents=True)
    for number in range(count):
        (notes / f"note-{number:04}.md").write_text(
            f"---\ntitle: Topic {number:04}\naliases: [shared]\n---\n# Heading\n{body}\n",
            encoding="utf-8",
        )


def test_lexical_query_caps_scanned_files_and_opens_no_bodies_for_title_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_notes(tmp_path, 1_000)
    store = LexicalIndexStore(tmp_path, _policy(), max_files_scanned=25)
    loads = 0
    original = store._load_metadata

    def counted(relative: str) -> object:
        nonlocal loads
        loads += 1
        return original(relative)

    monkeypatch.setattr(store, "_load_metadata", counted)
    candidates = store.search(MemoryQuery(titles=("topic",)), limit=10)

    assert loads == 25
    assert store.bodies_opened == 0
    assert len(candidates) == 10


def test_long_documents_produce_a_bounded_context_and_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_notes(tmp_path, 10, body="relevant " + "x" * 10_000)
    store = LexicalIndexStore(tmp_path, _policy())
    candidates = store.search(MemoryQuery(titles=("topic",)), limit=8)
    total_vault_chars = sum(
        len(path.read_text(encoding="utf-8")) for path in (tmp_path / "notes").glob("*.md")
    )
    excerpt_store = _Store({candidate.path: "relevant " + "x" * 10_000 for candidate in candidates})
    excerpts = tuple(
        excerpt_store.read_excerpt(candidate, max_chars=300) for candidate in candidates
    )
    retrieved_chars = sum(len(excerpt.text) for excerpt in excerpts)
    report = (
        f"retrieval efficiency: total_vault_chars={total_vault_chars} "
        f"retrieved_excerpt_chars={retrieved_chars} reduction_ratio="
        f"{1 - (retrieved_chars / total_vault_chars):.3f} selected_note_count={len(excerpts)}"
    )

    print(report)
    assert len(excerpts) == 8
    assert retrieved_chars <= 8 * 300
    assert "selected_note_count=8" in capsys.readouterr().out


@dataclass
class _Store:
    bodies: dict[str, str]
    reads: list[str] = field(default_factory=list)

    def search_lexical(self, query: MemoryQuery, *, limit: int) -> tuple[MemoryCandidate, ...]:
        return tuple(
            MemoryCandidate(path, path, (RetrievalMethod.LEXICAL_TITLE,), 1.0)
            for path in tuple(self.bodies)[:limit]
        )

    def read_excerpt(self, candidate: MemoryCandidate, *, max_chars: int) -> MemoryExcerpt:
        self.reads.append(candidate.path)
        text = self.bodies[candidate.path][:max_chars]
        return MemoryExcerpt(candidate.path, candidate.title, None, 1, 1, text, "h", False)

    def write_candidate(self, candidate: MemoryWriteCandidate) -> MemoryWriteResult:
        raise NotImplementedError

    def source_snapshot_hash(self) -> str:
        return "source"


@dataclass
class _HubIndex:
    direct: tuple[MemoryCandidate, ...]
    neighbor_calls: list[tuple[str, int, int]] = field(default_factory=list)

    def status(self) -> IndexStatus:
        return IndexStatus(IndexState.FRESH, "i", None, None, 0, 0, None, None)

    def search(self, query: MemoryQuery, *, limit: int) -> tuple[MemoryCandidate, ...]:
        return self.direct[:limit]

    def neighbors(self, path: str, *, depth: int, max_nodes: int) -> tuple[MemoryCandidate, ...]:
        self.neighbor_calls.append((path, depth, max_nodes))
        return tuple(
            MemoryCandidate(f"notes/n{number}.md", "n", (RetrievalMethod.STRUCTURAL_NEIGHBOR,), 1.0)
            for number in range(max_nodes + 100)
        )[:max_nodes]


def test_high_degree_graph_and_duplicate_aliases_remain_bounded() -> None:
    direct = tuple(
        MemoryCandidate("notes/hub.md", "hub", (RetrievalMethod.STRUCTURAL_NODE,), 1.0)
        for _ in range(20)
    )
    store = _Store({"notes/hub.md": "hub", **{f"notes/n{i}.md": "n" for i in range(5)}})
    index = _HubIndex(direct)
    retriever = MemoryRetriever(
        store,
        index,
        settings=MemoryRetrievalSettings(
            max_candidates=4,
            max_excerpts=3,
            max_excerpt_chars=10,
            max_total_context_chars=30,
            max_graph_depth=1,
            max_graph_nodes_visited=5,
        ),
    )

    context = retriever.retrieve(query=MemoryQuery(terms=("hub",)), source_snapshot_hash="source")

    assert index.neighbor_calls == [("notes/hub.md", 1, 5)]
    assert len(store.reads) <= 3
    assert len(context.excerpts) <= 3
    assert context.total_chars <= 30
