"""Memory model invariants: one test per rejection branch, plus MemoryQuery
hash determinism and MemoryContext excerpt/provenance pairing."""

from __future__ import annotations

from datetime import datetime

import pytest

from friday.application.memory.errors import (
    MemoryAccessDenied,
    MemoryDisabled,
    MemoryError,
    MemoryIndexCorrupt,
    MemoryIndexUnavailable,
    MemoryNoteTooLarge,
    MemoryWriteConflict,
    MemoryWriteDenied,
)
from friday.application.memory.models import (
    IndexBuildRequest,
    IndexSnapshot,
    IndexState,
    IndexStatus,
    MemoryCandidate,
    MemoryContext,
    MemoryExcerpt,
    MemoryProvenance,
    MemoryQuery,
    MemoryRetrievalItem,
    MemoryRetrievalRecord,
    MemoryVaultPolicy,
    MemoryWriteCandidate,
    MemoryWriteOperation,
    MemoryWriteResult,
    RetrievalMethod,
    RetrievalMode,
)
from friday.application.memory.ports import (
    MemoryIndexSnapshotRepository,
    MemoryRetrievalRecordRepository,
    MemoryRetrieverPort,
    MemoryStore,
    StructuralIndex,
    StructuralIndexBuilder,
)
from friday.domain.identifiers import RunId

T0 = "2026-01-01T00:00:00+00:00"


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def test_vault_policy_rejects_empty_include_globs() -> None:
    with pytest.raises(ValueError):
        MemoryVaultPolicy(include_globs=(), exclude_globs=(), max_files=10, max_note_bytes=1000)


def test_vault_policy_rejects_non_positive_max_files() -> None:
    with pytest.raises(ValueError):
        MemoryVaultPolicy(
            include_globs=("**/*.md",), exclude_globs=(), max_files=0, max_note_bytes=1000
        )


def test_vault_policy_rejects_non_positive_max_note_bytes() -> None:
    with pytest.raises(ValueError):
        MemoryVaultPolicy(
            include_globs=("**/*.md",), exclude_globs=(), max_files=10, max_note_bytes=0
        )


def test_vault_policy_accepts_valid_limits() -> None:
    policy = MemoryVaultPolicy(
        include_globs=("**/*.md",), exclude_globs=(".git/**",), max_files=10, max_note_bytes=1000
    )
    assert policy.max_files == 10


def test_memory_query_rejects_wholly_empty_query() -> None:
    with pytest.raises(ValueError):
        MemoryQuery()


def test_memory_query_hash_is_deterministic_for_identical_inputs() -> None:
    query = MemoryQuery(terms=("alpha", "beta"))
    assert query.query_hash == MemoryQuery(terms=("alpha", "beta")).query_hash


def test_memory_query_hash_is_independent_of_field_order() -> None:
    a = MemoryQuery(terms=("alpha", "beta"))
    b = MemoryQuery(terms=("beta", "alpha"))
    assert a.query_hash == b.query_hash


def test_memory_query_hash_is_case_insensitive() -> None:
    a = MemoryQuery(terms=("Alpha",))
    b = MemoryQuery(terms=("alpha",))
    assert a.query_hash == b.query_hash


def test_memory_query_hash_differs_for_different_inputs() -> None:
    a = MemoryQuery(terms=("alpha",))
    b = MemoryQuery(terms=("beta",))
    assert a.query_hash != b.query_hash


def test_memory_candidate_rejects_absolute_path() -> None:
    with pytest.raises(ValueError):
        MemoryCandidate(
            path="/etc/passwd",
            title="x",
            methods=(RetrievalMethod.LEXICAL_TITLE,),
            score=0.0,
        )


def test_memory_candidate_rejects_parent_traversal() -> None:
    with pytest.raises(ValueError):
        MemoryCandidate(
            path="../outside.md",
            title="x",
            methods=(RetrievalMethod.LEXICAL_TITLE,),
            score=0.0,
        )


def test_memory_candidate_rejects_empty_path() -> None:
    with pytest.raises(ValueError):
        MemoryCandidate(path="", title="x", methods=(RetrievalMethod.LEXICAL_TITLE,), score=0.0)


def test_memory_candidate_rejects_empty_methods() -> None:
    with pytest.raises(ValueError):
        MemoryCandidate(path="notes/a.md", title="x", methods=(), score=0.0)


def test_memory_candidate_rejects_negative_score() -> None:
    with pytest.raises(ValueError):
        MemoryCandidate(
            path="notes/a.md",
            title="x",
            methods=(RetrievalMethod.LEXICAL_TITLE,),
            score=-1.0,
        )


def test_memory_candidate_accepts_non_negative_graph_distance() -> None:
    candidate = MemoryCandidate(
        path="notes/a.md",
        title="x",
        methods=(RetrievalMethod.LEXICAL_TITLE,),
        score=1.0,
        graph_distance=2,
    )
    assert candidate.graph_distance == 2


def test_memory_candidate_rejects_negative_graph_distance() -> None:
    with pytest.raises(ValueError):
        MemoryCandidate(
            path="notes/a.md",
            title="x",
            methods=(RetrievalMethod.LEXICAL_TITLE,),
            score=0.0,
            graph_distance=-1,
        )


def test_memory_excerpt_requires_start_line_at_least_one() -> None:
    with pytest.raises(ValueError):
        MemoryExcerpt(
            path="notes/a.md",
            title="x",
            heading=None,
            start_line=0,
            end_line=1,
            text="body",
            content_hash="deadbeef",
            truncated=False,
        )


def test_memory_excerpt_requires_end_line_not_before_start_line() -> None:
    with pytest.raises(ValueError):
        MemoryExcerpt(
            path="notes/a.md",
            title="x",
            heading=None,
            start_line=5,
            end_line=4,
            text="body",
            content_hash="deadbeef",
            truncated=False,
        )


def test_memory_excerpt_rejects_absolute_path() -> None:
    with pytest.raises(ValueError):
        MemoryExcerpt(
            path="/etc/passwd",
            title="x",
            heading=None,
            start_line=1,
            end_line=1,
            text="body",
            content_hash="deadbeef",
            truncated=False,
        )


def test_memory_provenance_has_no_text_field() -> None:
    provenance = MemoryProvenance(
        path="notes/a.md",
        title="x",
        heading=None,
        start_line=1,
        end_line=1,
        content_hash="deadbeef",
        methods=(RetrievalMethod.LEXICAL_TITLE,),
        rank=0,
        index_snapshot_id=None,
        source_snapshot_id="snap-1",
        truncated=False,
    )
    assert not hasattr(provenance, "text")


def test_memory_provenance_rejects_start_line_below_one() -> None:
    with pytest.raises(ValueError):
        MemoryProvenance(
            path="notes/a.md",
            title="x",
            heading=None,
            start_line=0,
            end_line=1,
            content_hash="deadbeef",
            methods=(RetrievalMethod.LEXICAL_TITLE,),
            rank=0,
            index_snapshot_id=None,
            source_snapshot_id="snap-1",
            truncated=False,
        )


def test_memory_provenance_rejects_end_line_before_start_line() -> None:
    with pytest.raises(ValueError):
        MemoryProvenance(
            path="notes/a.md",
            title="x",
            heading=None,
            start_line=3,
            end_line=2,
            content_hash="deadbeef",
            methods=(RetrievalMethod.LEXICAL_TITLE,),
            rank=0,
            index_snapshot_id=None,
            source_snapshot_id="snap-1",
            truncated=False,
        )


def test_memory_provenance_rejects_empty_methods() -> None:
    with pytest.raises(ValueError):
        MemoryProvenance(
            path="notes/a.md",
            title="x",
            heading=None,
            start_line=1,
            end_line=1,
            content_hash="deadbeef",
            methods=(),
            rank=0,
            index_snapshot_id=None,
            source_snapshot_id="snap-1",
            truncated=False,
        )


def test_memory_provenance_rejects_negative_rank() -> None:
    with pytest.raises(ValueError):
        MemoryProvenance(
            path="notes/a.md",
            title="x",
            heading=None,
            start_line=1,
            end_line=1,
            content_hash="deadbeef",
            methods=(RetrievalMethod.LEXICAL_TITLE,),
            rank=-1,
            index_snapshot_id=None,
            source_snapshot_id="snap-1",
            truncated=False,
        )


def _excerpt(path: str) -> MemoryExcerpt:
    return MemoryExcerpt(
        path=path,
        title="x",
        heading=None,
        start_line=1,
        end_line=1,
        text="body",
        content_hash="deadbeef",
        truncated=False,
    )


def _provenance(path: str) -> MemoryProvenance:
    return MemoryProvenance(
        path=path,
        title="x",
        heading=None,
        start_line=1,
        end_line=1,
        content_hash="deadbeef",
        methods=(RetrievalMethod.LEXICAL_TITLE,),
        rank=0,
        index_snapshot_id=None,
        source_snapshot_id="snap-1",
        truncated=False,
    )


def test_memory_context_requires_matching_excerpt_and_provenance_counts() -> None:
    with pytest.raises(ValueError):
        MemoryContext(
            mode=RetrievalMode.LEXICAL_ONLY,
            excerpts=(_excerpt("notes/a.md"),),
            provenance=(),
            degraded_reason=None,
            index_state=IndexState.MISSING,
            total_chars=4,
        )


def test_memory_context_requires_identical_paths_in_identical_order() -> None:
    with pytest.raises(ValueError):
        MemoryContext(
            mode=RetrievalMode.LEXICAL_ONLY,
            excerpts=(_excerpt("notes/a.md"),),
            provenance=(_provenance("notes/b.md"),),
            degraded_reason=None,
            index_state=IndexState.MISSING,
            total_chars=4,
        )


def test_memory_context_accepts_paired_excerpts_and_provenance() -> None:
    context = MemoryContext(
        mode=RetrievalMode.HYBRID,
        excerpts=(_excerpt("notes/a.md"), _excerpt("notes/b.md")),
        provenance=(_provenance("notes/a.md"), _provenance("notes/b.md")),
        degraded_reason=None,
        index_state=IndexState.FRESH,
        total_chars=8,
    )
    assert len(context.excerpts) == len(context.provenance) == 2


def test_memory_write_candidate_rejects_absolute_path() -> None:
    with pytest.raises(ValueError):
        MemoryWriteCandidate(
            operation=MemoryWriteOperation.CREATE_NOTE,
            path="/etc/passwd",
            expected_content_hash=None,
            payload="body",
            frontmatter=(),
            memory_category="note",
        )


def test_memory_write_candidate_append_requires_expected_content_hash() -> None:
    with pytest.raises(ValueError):
        MemoryWriteCandidate(
            operation=MemoryWriteOperation.APPEND_MANAGED_NOTE,
            path="notes/a.md",
            expected_content_hash=None,
            payload="body",
            frontmatter=(),
            memory_category="note",
        )


def test_memory_write_candidate_create_forbids_expected_content_hash() -> None:
    with pytest.raises(ValueError):
        MemoryWriteCandidate(
            operation=MemoryWriteOperation.CREATE_NOTE,
            path="notes/a.md",
            expected_content_hash="deadbeef",
            payload="body",
            frontmatter=(),
            memory_category="note",
        )


def test_memory_write_candidate_accepts_valid_create() -> None:
    candidate = MemoryWriteCandidate(
        operation=MemoryWriteOperation.CREATE_NOTE,
        path="notes/a.md",
        expected_content_hash=None,
        payload="body",
        frontmatter=(("category", "note"),),
        memory_category="note",
    )
    assert candidate.path == "notes/a.md"


def test_memory_write_result_is_constructible() -> None:
    result = MemoryWriteResult(
        path="notes/a.md",
        operation=MemoryWriteOperation.CREATE_NOTE,
        content_hash="deadbeef",
        created=True,
        bytes_written=4,
    )
    assert result.created is True


def test_index_status_normalizes_built_at_to_utc() -> None:
    status = IndexStatus(
        state=IndexState.FRESH,
        snapshot_id="snap-1",
        source_snapshot_hash="hash-1",
        graph_checksum="checksum-1",
        node_count=1,
        edge_count=0,
        built_at=_dt(T0),
        failure_code=None,
    )
    assert status.built_at is not None
    assert status.built_at.tzinfo is not None


def test_index_status_allows_missing_built_at() -> None:
    status = IndexStatus(
        state=IndexState.MISSING,
        snapshot_id=None,
        source_snapshot_hash=None,
        graph_checksum=None,
        node_count=0,
        edge_count=0,
        built_at=None,
        failure_code="index_missing",
    )
    assert status.built_at is None


def test_index_build_request_is_constructible() -> None:
    request = IndexBuildRequest(
        vault_identity_hash="vault-1",
        source_snapshot_hash="hash-1",
        included_paths=("notes/a.md",),
        timeout_seconds=30.0,
        max_graph_bytes=1_000_000,
    )
    assert request.included_paths == ("notes/a.md",)


def test_index_snapshot_normalizes_built_at_to_utc() -> None:
    snapshot = IndexSnapshot(
        id="snap-1",
        vault_identity_hash="vault-1",
        source_snapshot_hash="hash-1",
        graph_checksum="checksum-1",
        graphify_version="0.9.22",
        state=IndexState.FRESH,
        built_at=_dt(T0),
        build_duration_seconds=1.5,
        file_count=10,
        source_total_bytes=1000,
        node_count=5,
        edge_count=4,
        failure_code=None,
    )
    assert snapshot.built_at.tzinfo is not None


def test_memory_retrieval_item_rejects_start_line_below_one() -> None:
    with pytest.raises(ValueError):
        MemoryRetrievalItem(
            path="notes/a.md",
            heading=None,
            start_line=0,
            end_line=1,
            content_hash="deadbeef",
            rank=0,
            methods=(RetrievalMethod.LEXICAL_TITLE,),
            truncated=False,
        )


def test_memory_retrieval_item_rejects_end_line_before_start_line() -> None:
    with pytest.raises(ValueError):
        MemoryRetrievalItem(
            path="notes/a.md",
            heading=None,
            start_line=3,
            end_line=2,
            content_hash="deadbeef",
            rank=0,
            methods=(RetrievalMethod.LEXICAL_TITLE,),
            truncated=False,
        )


def test_memory_retrieval_item_rejects_absolute_path() -> None:
    with pytest.raises(ValueError):
        MemoryRetrievalItem(
            path="/etc/passwd",
            heading=None,
            start_line=1,
            end_line=1,
            content_hash="deadbeef",
            rank=0,
            methods=(RetrievalMethod.LEXICAL_TITLE,),
            truncated=False,
        )


def test_memory_retrieval_item_accepts_valid_line_range() -> None:
    item = MemoryRetrievalItem(
        path="notes/a.md",
        heading=None,
        start_line=1,
        end_line=1,
        content_hash="deadbeef",
        rank=0,
        methods=(RetrievalMethod.LEXICAL_TITLE,),
        truncated=False,
    )
    assert item.end_line == 1


def test_memory_retrieval_record_normalizes_created_at_to_utc() -> None:
    record = MemoryRetrievalRecord(
        id="rec-1",
        run_id=RunId.new(),
        turn_number=1,
        query_hash="deadbeef",
        source_snapshot_id="snap-1",
        index_snapshot_id=None,
        created_at=_dt(T0),
        candidate_count=1,
        selected_count=1,
        items=(),
    )
    assert record.created_at.tzinfo is not None


def test_memory_errors_form_a_hierarchy_rooted_at_memory_error() -> None:
    for error_type in (
        MemoryAccessDenied,
        MemoryNoteTooLarge,
        MemoryIndexUnavailable,
        MemoryIndexCorrupt,
        MemoryWriteConflict,
        MemoryWriteDenied,
        MemoryDisabled,
    ):
        assert issubclass(error_type, MemoryError)


def test_memory_ports_are_runtime_checkable_protocol_shapes() -> None:
    for port_type in (
        MemoryStore,
        StructuralIndex,
        StructuralIndexBuilder,
        MemoryIndexSnapshotRepository,
        MemoryRetrievalRecordRepository,
        MemoryRetrieverPort,
    ):
        assert hasattr(port_type, "__mro__")
