from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

# mypy: disable-error-code=no-untyped-def
from typing import Any

import pytest

from friday.application.memory.models import IndexState, MemoryQuery, MemoryVaultPolicy
from friday.infrastructure.memory.graphify_json import (
    GraphifyJsonIndex,
    GraphifyJsonIndexSettings,
    _check_graph_shape,
    _check_path_safe,
)
from friday.infrastructure.memory.index_metadata import IndexMetadata
from friday.infrastructure.memory.obsidian_vault import ObsidianVaultStore

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_A_NODE: dict[str, Any] = {
    "id": "n1",
    "label": "Alpha Note",
    "norm_label": "alpha note",
    "file_type": "document",
    "source_file": "00-Inbox/alpha.md",
    "source_location": "L1",
    "community": "c1",
    "_origin": "extract",
}
_B_NODE: dict[str, Any] = {
    "id": "n2",
    "label": "Beta Note",
    "norm_label": "beta note",
    "file_type": "document",
    "source_file": "10-Projects/beta.md",
    "source_location": "L1",
    "community": "c1",
    "_origin": "extract",
}
_C_NODE: dict[str, Any] = {
    "id": "n3",
    "label": "Gamma Doc",
    "norm_label": "gamma doc",
    "file_type": "document",
    "source_file": "20-Areas/gamma.md",
    "source_location": "L1",
    "community": "c2",
    "_origin": "extract",
}
_CODE_NODE: dict[str, Any] = {
    "id": "n4",
    "label": "Code File",
    "norm_label": "code file",
    "file_type": "code",
    "source_file": "src/main.py",
    "source_location": "L1",
    "community": "c1",
    "_origin": "extract",
}
_CONCEPT_NODE: dict[str, Any] = {
    "id": "n5",
    "label": "Some Concept",
    "norm_label": "some concept",
    "file_type": "concept",
    "source_file": "30-Resources/concept.md",
    "source_location": "L1",
    "community": "c2",
    "_origin": "extract",
}
_NON_MD_NODE: dict[str, Any] = {
    "id": "n6",
    "label": "Image Asset",
    "norm_label": "image asset",
    "file_type": "document",
    "source_file": "Attachments/photo.png",
    "source_location": "L1",
    "community": "c3",
    "_origin": "extract",
}
_NO_SOURCE_NODE: dict[str, Any] = {
    "id": "n7",
    "label": "No Source",
    "norm_label": "no source",
    "file_type": "document",
}

_REFERENCE_LINK: dict[str, Any] = {
    "source": "n1",
    "target": "n2",
    "relation": "references",
    "confidence": "high",
    "confidence_score": 0.95,
    "weight": 1.0,
    "source_file": "00-Inbox/alpha.md",
    "source_location": "L2",
}
_CONTAINS_LINK: dict[str, Any] = {
    "source": "n2",
    "target": "n3",
    "relation": "contains",
    "confidence": "high",
    "confidence_score": 0.9,
    "weight": 1.0,
    "source_file": "10-Projects/beta.md",
    "source_location": "L3",
}
_DEFINES_LINK: dict[str, Any] = {  # NOT in _KNOWN_RELATIONS
    "source": "n1",
    "target": "n3",
    "relation": "defines",
    "confidence": "medium",
    "confidence_score": 0.7,
    "weight": 1.0,
    "source_file": "00-Inbox/alpha.md",
    "source_location": "L4",
}


def _make_settings(
    tmp_path: Path,
    *,
    vault_identity_hash: str = "test-vault-hash",
    max_graph_bytes: int = 1_000_000,
) -> GraphifyJsonIndexSettings:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    indexes = tmp_path / "indexes"
    return GraphifyJsonIndexSettings(
        vault_root=vault,
        index_root=indexes,
        vault_identity_hash=vault_identity_hash,
        max_graph_bytes=max_graph_bytes,
    )


def _store_for(vault_root: Path) -> ObsidianVaultStore:
    """A permissive store mirroring what the real ObsidianVaultStore would
    compute -- used so tests can supply GraphifyJsonIndex the same curated
    source-set hash the builder would have stamped into metadata."""
    return ObsidianVaultStore(vault_root, MemoryVaultPolicy(("**/*.md",), (), 10_000, 10_000_000))


def _active_dir(settings: GraphifyJsonIndexSettings) -> Path:
    return settings.index_root / settings.vault_identity_hash[:32] / "active"


def _write_graph(
    active: Path,
    nodes: list[Any] | None = None,
    links: list[Any] | None = None,
    *,
    extra_keys: dict[str, Any] | None = None,
    raw_text: str | None = None,
) -> Path:
    active.mkdir(parents=True, exist_ok=True)
    path = active / "graph.json"
    if raw_text is not None:
        path.write_text(raw_text, encoding="utf-8")
        return path
    data = {
        "directed": False,
        "multigraph": False,
        "graph": {},
        "nodes": nodes or [],
        "links": links or [],
        "hyperedges": [],
        "built_at_commit": "abc123",
    }
    if extra_keys:
        data.update(extra_keys)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _write_metadata(
    active: Path,
    *,
    source_snapshot_hash: str = "test-source-hash",
    graph_checksum: str = "graph-chk",
    node_count: int = 0,
    edge_count: int = 0,
) -> None:
    active.mkdir(parents=True, exist_ok=True)
    meta = IndexMetadata(
        schema_version=1,
        graphify_version="0.9.22",
        vault_identity_hash="test-hash",
        source_snapshot_hash=source_snapshot_hash,
        included_file_count=1,
        source_total_bytes=10,
        built_at=datetime(2026, 1, 1, tzinfo=UTC),
        build_duration_seconds=0.5,
        graph_checksum=graph_checksum,
        node_count=node_count,
        edge_count=edge_count,
    )
    meta.write(active)


def _make_vault_md(vault: Path, *rel_paths: str) -> None:
    for rp in rel_paths:
        fp = vault / rp
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(f"content of {rp}", encoding="utf-8")


def _empty_hash() -> str:
    return hashlib.sha256(b"").hexdigest()


# ---------------------------------------------------------------------------
# GraphifyJsonIndexSettings
# ---------------------------------------------------------------------------


def test_settings_rejects_zero_max_graph_bytes():
    with pytest.raises(ValueError, match="positive"):
        GraphifyJsonIndexSettings(
            vault_root=Path("/x"), index_root=Path("/y"), vault_identity_hash="h", max_graph_bytes=0
        )


def test_settings_rejects_negative_max_graph_bytes():
    with pytest.raises(ValueError, match="positive"):
        GraphifyJsonIndexSettings(
            vault_root=Path("/x"),
            index_root=Path("/y"),
            vault_identity_hash="h",
            max_graph_bytes=-1,
        )


# ---------------------------------------------------------------------------
# _check_path_safe
# ---------------------------------------------------------------------------


def test_check_path_safe_empty():
    assert _check_path_safe("") == "empty_source_file"


def test_check_path_safe_absolute():
    assert _check_path_safe("/etc/passwd") == "absolute_source_file"


def test_check_path_safe_tilde():
    assert _check_path_safe("~/Documents/note.md") == "absolute_source_file"


def test_check_path_safe_traversal():
    assert _check_path_safe("00-Inbox/../../etc") == "path_escapes_vault"


def test_check_path_safe_valid():
    assert _check_path_safe("00-Inbox/note.md") is None


# ---------------------------------------------------------------------------
# _check_graph_shape
# ---------------------------------------------------------------------------


def test_graph_shape_nodes_not_a_list():
    assert _check_graph_shape({"nodes": "bad", "links": []}) == "nodes_not_a_list"


def test_graph_shape_links_not_a_list():
    assert _check_graph_shape({"nodes": [], "links": "bad"}) == "links_not_a_list"


def test_graph_shape_missing_node_id():
    assert (
        _check_graph_shape({"nodes": [{"label": "x"}], "links": []}) == "missing_or_invalid_node_id"
    )


def test_graph_shape_node_id_not_string():
    assert _check_graph_shape({"nodes": [{"id": 123}], "links": []}) == "missing_or_invalid_node_id"


def test_graph_shape_duplicate_node_id():
    nodes = [{"id": "n1"}, {"id": "n1"}]
    assert _check_graph_shape({"nodes": nodes, "links": []}) == "duplicate_node_id"


def test_graph_shape_non_string_source_file():
    nodes = [{"id": "n1", "source_file": 42}]
    assert _check_graph_shape({"nodes": nodes, "links": []}) == "non_string_source_file"


def test_graph_shape_absolute_source_file():
    nodes = [{"id": "n1", "source_file": "/etc/passwd"}]
    assert _check_graph_shape({"nodes": nodes, "links": []}) == "absolute_source_file"


def test_graph_shape_path_escapes(tmp_path):
    nodes = [{"id": "n1", "source_file": "00-Inbox/../../outside"}]
    assert _check_graph_shape({"nodes": nodes, "links": []}) == "path_escapes_vault"


def test_graph_shape_missing_edge_keys():
    nodes = [{"id": "n1"}, {"id": "n2"}]
    links = [{"source": "n1"}]  # missing target
    assert _check_graph_shape({"nodes": nodes, "links": links}) == "missing_edge_source_or_target"


def test_graph_shape_dangling_edge_source():
    nodes = [{"id": "n1"}]
    links = [{"source": "n99", "target": "n1"}]
    assert _check_graph_shape({"nodes": nodes, "links": links}) == "dangling_edge_source"


def test_graph_shape_dangling_edge_target():
    nodes = [{"id": "n1"}]
    links = [{"source": "n1", "target": "n99"}]
    assert _check_graph_shape({"nodes": nodes, "links": links}) == "dangling_edge_target"


def test_graph_shape_valid():
    nodes = [{"id": "n1"}, {"id": "n2"}]
    links = [{"source": "n1", "target": "n2"}]
    assert _check_graph_shape({"nodes": nodes, "links": links}) is None


# ---------------------------------------------------------------------------
# status() — MISSING
# ---------------------------------------------------------------------------


def test_status_missing_when_active_dir_absent(tmp_path):
    settings = _make_settings(tmp_path)
    idx = GraphifyJsonIndex(settings, _store_for(settings.vault_root))
    st = idx.status()
    assert st.state is IndexState.MISSING
    assert st.failure_code == "index_missing"


def test_status_missing_when_graph_file_absent(tmp_path):
    settings = _make_settings(tmp_path)
    active = _active_dir(settings)
    active.mkdir(parents=True)
    idx = GraphifyJsonIndex(settings, _store_for(settings.vault_root))
    st = idx.status()
    assert st.state is IndexState.MISSING
    assert st.failure_code == "index_missing"


# ---------------------------------------------------------------------------
# status() — CORRUPT (size / metadata / graph)
# ---------------------------------------------------------------------------


def test_status_corrupt_oversized_graph(tmp_path):
    settings = _make_settings(tmp_path, max_graph_bytes=5)
    active = _active_dir(settings)
    _write_graph(active, raw_text='{"x":1}')
    idx = GraphifyJsonIndex(settings, _store_for(settings.vault_root))
    st = idx.status()
    assert st.state is IndexState.CORRUPT
    assert st.failure_code == "oversized_graph"


def test_status_corrupt_invalid_metadata(tmp_path):
    settings = _make_settings(tmp_path)
    active = _active_dir(settings)
    _write_graph(active, nodes=[], links=[])
    # Write a truncated metadata file
    active.mkdir(parents=True, exist_ok=True)
    (active / "index-metadata.json").write_text("{truncated", encoding="utf-8")
    idx = GraphifyJsonIndex(settings, _store_for(settings.vault_root))
    st = idx.status()
    assert st.state is IndexState.CORRUPT
    assert st.failure_code == "invalid_metadata"


def test_status_corrupt_invalid_json(tmp_path):
    settings = _make_settings(tmp_path)
    active = _active_dir(settings)
    _write_metadata(active)
    _write_graph(active, raw_text="not json")
    idx = GraphifyJsonIndex(settings, _store_for(settings.vault_root))
    st = idx.status()
    assert st.state is IndexState.CORRUPT
    assert st.failure_code == "invalid_json"


def test_status_corrupt_invalid_utf8(tmp_path):
    settings = _make_settings(tmp_path)
    active = _active_dir(settings)
    _write_metadata(active)
    graph_path = active / "graph.json"
    graph_path.write_bytes(b"\xff\xfe\x00\x01")
    idx = GraphifyJsonIndex(settings, _store_for(settings.vault_root))
    st = idx.status()
    assert st.state is IndexState.CORRUPT
    assert st.failure_code == "invalid_utf8"


def test_status_corrupt_not_an_object(tmp_path):
    settings = _make_settings(tmp_path)
    active = _active_dir(settings)
    _write_metadata(active)
    _write_graph(active, raw_text="[]")
    idx = GraphifyJsonIndex(settings, _store_for(settings.vault_root))
    st = idx.status()
    assert st.state is IndexState.CORRUPT


def test_status_corrupt_wrong_top_level_keys(tmp_path):
    settings = _make_settings(tmp_path)
    active = _active_dir(settings)
    _write_metadata(active)
    _write_graph(active, raw_text='{"nodes":[],"extra":true}')
    idx = GraphifyJsonIndex(settings, _store_for(settings.vault_root))
    st = idx.status()
    assert st.state is IndexState.CORRUPT
    assert st.failure_code == "invalid_top_level_keys"


def test_status_corrupt_extra_top_level_key(tmp_path):
    settings = _make_settings(tmp_path)
    active = _active_dir(settings)
    _write_metadata(active)
    data = {
        "directed": False,
        "multigraph": False,
        "graph": {},
        "nodes": [],
        "links": [],
        "hyperedges": [],
        "built_at_commit": "x",
        "unexpected": True,
    }
    _write_graph(active, raw_text=json.dumps(data))
    idx = GraphifyJsonIndex(settings, _store_for(settings.vault_root))
    st = idx.status()
    assert st.state is IndexState.CORRUPT
    assert st.failure_code == "invalid_top_level_keys"


def test_status_corrupt_duplicate_node_id(tmp_path):
    settings = _make_settings(tmp_path)
    active = _active_dir(settings)
    _write_metadata(active)
    nodes = [{"id": "n1"}, {"id": "n1"}]
    _write_graph(active, nodes=nodes, links=[])
    idx = GraphifyJsonIndex(settings, _store_for(settings.vault_root))
    st = idx.status()
    assert st.state is IndexState.CORRUPT
    assert st.failure_code == "duplicate_node_id"


def test_status_corrupt_dangling_edge(tmp_path):
    settings = _make_settings(tmp_path)
    active = _active_dir(settings)
    _write_metadata(active)
    nodes = [{"id": "n1"}]
    links = [{"source": "n1", "target": "n99"}]
    _write_graph(active, nodes=nodes, links=links)
    idx = GraphifyJsonIndex(settings, _store_for(settings.vault_root))
    st = idx.status()
    assert st.state is IndexState.CORRUPT
    assert st.failure_code == "dangling_edge_target"


def test_status_corrupt_node_id_non_string(tmp_path):
    settings = _make_settings(tmp_path)
    active = _active_dir(settings)
    _write_metadata(active)
    _write_graph(active, nodes=[{"id": 7}], links=[])
    idx = GraphifyJsonIndex(settings, _store_for(settings.vault_root))
    st = idx.status()
    assert st.state is IndexState.CORRUPT
    assert st.failure_code == "missing_or_invalid_node_id"


# ---------------------------------------------------------------------------
# status() — FRESH / STALE
# ---------------------------------------------------------------------------


def test_status_fresh(tmp_path):
    settings = _make_settings(tmp_path)
    active = _active_dir(settings)
    vault = settings.vault_root
    _make_vault_md(vault, "00-Inbox/alpha.md")
    source_hash = _store_for(vault).source_snapshot_hash()
    nodes = [{**{"id": "n1"}, **_A_NODE}]
    _write_graph(active, nodes=nodes, links=[])
    _write_metadata(active, source_snapshot_hash=source_hash, node_count=1)
    idx = GraphifyJsonIndex(settings, _store_for(settings.vault_root))
    st = idx.status()
    assert st.state is IndexState.FRESH
    assert st.failure_code is None


def test_status_stale(tmp_path):
    settings = _make_settings(tmp_path)
    active = _active_dir(settings)
    vault = settings.vault_root
    _make_vault_md(vault, "00-Inbox/alpha.md")
    nodes = [{**{"id": "n1"}, **_A_NODE}]
    _write_graph(active, nodes=nodes, links=[])
    _write_metadata(active, source_snapshot_hash="old-hash", node_count=1)
    idx = GraphifyJsonIndex(settings, _store_for(settings.vault_root))
    st = idx.status()
    assert st.state is IndexState.STALE


def test_status_fresh_no_md_files_empty_hash(tmp_path):
    settings = _make_settings(tmp_path)
    active = _active_dir(settings)
    _write_graph(active, nodes=[], links=[])
    empty = _empty_hash()
    _write_metadata(active, source_snapshot_hash=empty)
    idx = GraphifyJsonIndex(settings, _store_for(settings.vault_root))
    st = idx.status()
    assert st.state is IndexState.FRESH
    assert st.failure_code is None


# ---------------------------------------------------------------------------
# status() — metadata fields preserved
# ---------------------------------------------------------------------------


def test_status_preserves_metadata_fields(tmp_path):
    settings = _make_settings(tmp_path)
    active = _active_dir(settings)
    vault = settings.vault_root
    _make_vault_md(vault, "00-Inbox/a.md")
    source_hash = _store_for(vault).source_snapshot_hash()
    nodes = [{**{"id": "n1"}, **_A_NODE}]
    _write_graph(active, nodes=nodes, links=[])
    _write_metadata(
        active, source_snapshot_hash=source_hash, node_count=1, edge_count=0, graph_checksum="chk-1"
    )
    idx = GraphifyJsonIndex(settings, _store_for(settings.vault_root))
    st = idx.status()
    assert st.state is IndexState.FRESH
    assert st.graph_checksum == "chk-1"
    assert st.node_count == 1
    assert st.edge_count == 0


# ---------------------------------------------------------------------------
# search()
# ---------------------------------------------------------------------------


def _make_index_with_nodes(
    tmp_path: Path,
    nodes: list[Any],
    links: list[Any] | None = None,
    *,
    vault_identity_hash: str = "test-vault-hash",
) -> GraphifyJsonIndex:
    settings = _make_settings(tmp_path, vault_identity_hash=vault_identity_hash)
    active = _active_dir(settings)
    _write_graph(active, nodes=nodes, links=links or [])
    return GraphifyJsonIndex(settings, _store_for(settings.vault_root))


def test_search_title_exact_match(tmp_path):
    idx = _make_index_with_nodes(tmp_path, [_A_NODE, _B_NODE])
    candidates = idx.search(MemoryQuery(titles=("Alpha Note",)), limit=10)
    assert len(candidates) == 1
    assert candidates[0].path == "00-Inbox/alpha.md"


def test_search_norm_label_match(tmp_path):
    idx = _make_index_with_nodes(tmp_path, [_A_NODE, _B_NODE])
    candidates = idx.search(MemoryQuery(titles=("alpha note",)), limit=10)
    assert len(candidates) == 1
    assert candidates[0].path == "00-Inbox/alpha.md"


def test_search_term_substring(tmp_path):
    idx = _make_index_with_nodes(tmp_path, [_A_NODE, _B_NODE])
    candidates = idx.search(MemoryQuery(terms=("bet",)), limit=10)
    assert len(candidates) == 1
    assert candidates[0].path == "10-Projects/beta.md"


def test_search_phrase_substring(tmp_path):
    idx = _make_index_with_nodes(tmp_path, [_A_NODE, _B_NODE, _C_NODE])
    candidates = idx.search(MemoryQuery(phrases=("gamma",)), limit=10)
    assert len(candidates) == 1
    assert candidates[0].path == "20-Areas/gamma.md"


def test_search_no_match(tmp_path):
    idx = _make_index_with_nodes(tmp_path, [_A_NODE])
    candidates = idx.search(MemoryQuery(titles=("Zulu",)), limit=10)
    assert len(candidates) == 0


def test_search_empty_query(tmp_path):
    idx = _make_index_with_nodes(tmp_path, [_A_NODE])
    with pytest.raises(ValueError, match="empty"):
        idx.search(MemoryQuery(), limit=10)


def test_search_limit(tmp_path):
    nodes = [_A_NODE, _B_NODE, _C_NODE]
    idx = _make_index_with_nodes(tmp_path, nodes)
    candidates = idx.search(MemoryQuery(terms=("note", "doc")), limit=2)
    assert len(candidates) == 2


def test_search_limit_on_title_matches(tmp_path):
    a2 = {**_A_NODE, "id": "n10", "source_file": "00-Inbox/alpha2.md"}
    nodes = [_A_NODE, _B_NODE, a2]
    idx = _make_index_with_nodes(tmp_path, nodes)
    candidates = idx.search(MemoryQuery(titles=("Alpha Note", "Beta Note")), limit=1)
    assert len(candidates) == 1


def test_search_excludes_non_document_nodes(tmp_path):
    nodes = [_A_NODE, _CODE_NODE, _CONCEPT_NODE]
    idx = _make_index_with_nodes(tmp_path, nodes)
    candidates = idx.search(MemoryQuery(titles=("Code File", "Alpha Note")), limit=10)
    assert len(candidates) == 1
    assert candidates[0].path == "00-Inbox/alpha.md"


def test_search_excludes_non_markdown_files(tmp_path):
    nodes = [_A_NODE, _NON_MD_NODE]
    idx = _make_index_with_nodes(tmp_path, nodes)
    candidates = idx.search(MemoryQuery(titles=("Image Asset", "Alpha Note")), limit=10)
    assert len(candidates) == 1
    assert candidates[0].path == "00-Inbox/alpha.md"


def test_search_excludes_node_without_source_file(tmp_path):
    nodes = [_A_NODE, _NO_SOURCE_NODE]
    idx = _make_index_with_nodes(tmp_path, nodes)
    candidates = idx.search(MemoryQuery(titles=("No Source", "Alpha Note")), limit=10)
    assert len(candidates) == 1
    assert candidates[0].path == "00-Inbox/alpha.md"


def test_search_candidate_has_correct_fields(tmp_path):
    idx = _make_index_with_nodes(tmp_path, [_A_NODE])
    candidates = idx.search(MemoryQuery(titles=("Alpha Note",)), limit=10)
    assert len(candidates) == 1
    c = candidates[0]
    assert c.path == "00-Inbox/alpha.md"
    assert c.title == "Alpha Note"
    assert len(c.methods) == 1
    assert c.score == 1.0
    assert c.graph_distance is None


def test_search_when_graph_corrupt_returns_empty(tmp_path):
    settings = _make_settings(tmp_path)
    active = _active_dir(settings)
    active.mkdir(parents=True)
    (active / "graph.json").write_text("corrupt", encoding="utf-8")
    idx = GraphifyJsonIndex(settings, _store_for(settings.vault_root))
    candidates = idx.search(MemoryQuery(titles=("x",)), limit=10)
    assert len(candidates) == 0


def test_search_deduplicates_by_path(tmp_path):
    a1 = {**_A_NODE, "id": "n1", "source_file": "00-Inbox/alpha.md"}
    a2 = {**_A_NODE, "id": "n2", "source_file": "00-Inbox/alpha.md"}
    idx = _make_index_with_nodes(tmp_path, [a1, a2])
    candidates = idx.search(MemoryQuery(titles=("Alpha Note",)), limit=10)
    assert len(candidates) == 1


def test_search_case_insensitive_title(tmp_path):
    idx = _make_index_with_nodes(tmp_path, [_A_NODE])
    candidates = idx.search(MemoryQuery(titles=("alpha note",)), limit=10)
    assert len(candidates) == 1


# ---------------------------------------------------------------------------
# neighbors()
# ---------------------------------------------------------------------------


def _make_index_with_graph(
    tmp_path: Path,
    nodes: list[Any],
    links: list[Any] | None = None,
    *,
    vault_identity_hash: str = "test-vault-hash",
) -> GraphifyJsonIndex:
    return _make_index_with_nodes(tmp_path, nodes, links, vault_identity_hash=vault_identity_hash)


def test_neighbors_direct(tmp_path):
    nodes = [_A_NODE, _B_NODE, _C_NODE]
    links = [_REFERENCE_LINK]
    idx = _make_index_with_graph(tmp_path, nodes, links)
    result = idx.neighbors("00-Inbox/alpha.md", depth=1, max_nodes=10)
    assert len(result) == 1
    assert result[0].path == "10-Projects/beta.md"
    assert result[0].graph_distance == 1


def test_neighbors_backlink(tmp_path):
    # beta has an incoming link FROM alpha, so beta -> alpha is a backlink
    nodes = [_A_NODE, _B_NODE, _C_NODE]
    links = [_REFERENCE_LINK]  # n1 -> n2
    idx = _make_index_with_graph(tmp_path, nodes, links)
    result = idx.neighbors("10-Projects/beta.md", depth=1, max_nodes=10)
    assert len(result) == 1
    assert result[0].path == "00-Inbox/alpha.md"
    assert result[0].graph_distance == 1


def test_neighbors_depth_cap(tmp_path):
    nodes = [_A_NODE, _B_NODE, _C_NODE]
    links = [_REFERENCE_LINK, _CONTAINS_LINK]  # n1->n2, n2->n3
    idx = _make_index_with_graph(tmp_path, nodes, links)
    result = idx.neighbors("00-Inbox/alpha.md", depth=1, max_nodes=10)
    assert len(result) == 1
    assert result[0].path == "10-Projects/beta.md"


def test_neighbors_depth_2(tmp_path):
    nodes = [_A_NODE, _B_NODE, _C_NODE]
    links = [_REFERENCE_LINK, _CONTAINS_LINK]  # n1->n2, n2->n3
    idx = _make_index_with_graph(tmp_path, nodes, links)
    result = idx.neighbors("00-Inbox/alpha.md", depth=2, max_nodes=10)
    assert len(result) == 2
    paths = {c.path for c in result}
    assert "10-Projects/beta.md" in paths
    assert "20-Areas/gamma.md" in paths


def test_neighbors_max_nodes_cap(tmp_path):
    nodes = [_A_NODE, _B_NODE, _C_NODE]
    links = [_REFERENCE_LINK, _CONTAINS_LINK]
    idx = _make_index_with_graph(tmp_path, nodes, links)
    result = idx.neighbors("00-Inbox/alpha.md", depth=2, max_nodes=1)
    assert len(result) == 1


def test_neighbors_missing_start_node(tmp_path):
    nodes = [_A_NODE, _B_NODE]
    links = [_REFERENCE_LINK]
    idx = _make_index_with_graph(tmp_path, nodes, links)
    result = idx.neighbors("nonexistent.md", depth=1, max_nodes=10)
    assert len(result) == 0


def test_neighbors_deterministic_ordering(tmp_path):
    nodes = [_A_NODE, _B_NODE, _C_NODE]
    links = [_REFERENCE_LINK, _CONTAINS_LINK]
    idx = _make_index_with_graph(tmp_path, nodes, links)
    r1 = idx.neighbors("00-Inbox/alpha.md", depth=2, max_nodes=10)
    r2 = idx.neighbors("00-Inbox/alpha.md", depth=2, max_nodes=10)
    assert [c.path for c in r1] == [c.path for c in r2]


def test_neighbors_excludes_non_document_nodes(tmp_path):
    code_link = {"source": "n1", "target": "n4", "relation": "references"}
    nodes = [_A_NODE, _B_NODE, _CODE_NODE]
    links = [_REFERENCE_LINK, code_link]
    idx = _make_index_with_graph(tmp_path, nodes, links)
    result = idx.neighbors("00-Inbox/alpha.md", depth=1, max_nodes=10)
    assert len(result) == 1
    assert result[0].path == "10-Projects/beta.md"


def test_neighbors_excludes_non_markdown(tmp_path):
    md_link = {"source": "n1", "target": "n6", "relation": "references"}
    nodes = [_A_NODE, _B_NODE, _NON_MD_NODE]
    links = [_REFERENCE_LINK, md_link]
    idx = _make_index_with_graph(tmp_path, nodes, links)
    result = idx.neighbors("00-Inbox/alpha.md", depth=1, max_nodes=10)
    assert len(result) == 1
    assert result[0].path == "10-Projects/beta.md"


def test_neighbors_when_graph_corrupt_returns_empty(tmp_path):
    settings = _make_settings(tmp_path)
    active = _active_dir(settings)
    active.mkdir(parents=True)
    (active / "graph.json").write_text("bad", encoding="utf-8")
    idx = GraphifyJsonIndex(settings, _store_for(settings.vault_root))
    result = idx.neighbors("x.md", depth=1, max_nodes=10)
    assert len(result) == 0


def test_neighbors_distance_set_on_all(tmp_path):
    nodes = [_A_NODE, _B_NODE, _C_NODE]
    links = [_REFERENCE_LINK, _CONTAINS_LINK]
    idx = _make_index_with_graph(tmp_path, nodes, links)
    result = idx.neighbors("00-Inbox/alpha.md", depth=2, max_nodes=10)
    for c in result:
        assert c.graph_distance is not None
        assert c.graph_distance >= 1


def test_neighbors_no_edges_returns_empty(tmp_path):
    nodes = [_A_NODE, _B_NODE]
    idx = _make_index_with_graph(tmp_path, nodes, [])
    result = idx.neighbors("00-Inbox/alpha.md", depth=1, max_nodes=10)
    assert len(result) == 0


def test_neighbors_only_known_relations_traversed(tmp_path):
    # defines is NOT in _KNOWN_RELATIONS - n1->n3 should be excluded
    nodes = [_A_NODE, _B_NODE, _C_NODE]
    links = [_REFERENCE_LINK, _DEFINES_LINK]
    idx = _make_index_with_graph(tmp_path, nodes, links)
    result = idx.neighbors("00-Inbox/alpha.md", depth=1, max_nodes=10)
    assert len(result) == 1
    assert result[0].path == "10-Projects/beta.md"


def test_neighbors_does_not_include_start_node(tmp_path):
    nodes = [
        {"id": "n1", "label": "Self", "file_type": "document", "source_file": "00-Inbox/self.md"}
    ]
    # Self-link
    links = [{"source": "n1", "target": "n1", "relation": "references"}]
    idx = _make_index_with_graph(tmp_path, nodes, links)
    result = idx.neighbors("00-Inbox/self.md", depth=2, max_nodes=10)
    assert len(result) == 0


def test_neighbors_skips_node_without_source_file(tmp_path):
    nodes = [
        {"id": "n1", "label": "A", "file_type": "document", "source_file": "00-Inbox/a.md"},
        {"id": "n2", "label": "B", "file_type": "document"},
    ]
    links = [{"source": "n1", "target": "n2", "relation": "references"}]
    idx = _make_index_with_graph(tmp_path, nodes, links)
    result = idx.neighbors("00-Inbox/a.md", depth=1, max_nodes=10)
    assert len(result) == 0


# ---------------------------------------------------------------------------
# Edge cases for uncovered branches
# ---------------------------------------------------------------------------


def test_search_when_graph_missing(tmp_path):
    settings = _make_settings(tmp_path)
    idx = GraphifyJsonIndex(settings, _store_for(settings.vault_root))
    candidates = idx.search(MemoryQuery(titles=("x",)), limit=10)
    assert len(candidates) == 0


def test_search_when_graph_oversized(tmp_path):
    settings = _make_settings(tmp_path, max_graph_bytes=5)
    active = _active_dir(settings)
    _write_graph(active, nodes=[_A_NODE], links=[])
    idx = GraphifyJsonIndex(settings, _store_for(settings.vault_root))
    candidates = idx.search(MemoryQuery(titles=("Alpha Note",)), limit=10)
    assert len(candidates) == 0


def test_neighbors_when_graph_missing(tmp_path):
    settings = _make_settings(tmp_path)
    idx = GraphifyJsonIndex(settings, _store_for(settings.vault_root))
    result = idx.neighbors("x.md", depth=1, max_nodes=10)
    assert len(result) == 0


def test_search_deduplicates_terms(tmp_path):
    a1 = {**_A_NODE, "id": "n1", "label": "Dup Target", "source_file": "00-Inbox/a.md"}
    a2 = {**_A_NODE, "id": "n2", "label": "Dup Target", "source_file": "00-Inbox/a.md"}
    idx = _make_index_with_nodes(tmp_path, [a1, a2])
    candidates = idx.search(MemoryQuery(terms=("Dup",)), limit=10)
    assert len(candidates) == 1


def test_search_deduplicates_phrases(tmp_path):
    a1 = {**_A_NODE, "id": "n1", "label": "Same Label", "source_file": "00-Inbox/a.md"}
    a2 = {**_A_NODE, "id": "n2", "label": "Same Label", "source_file": "00-Inbox/a.md"}
    idx = _make_index_with_nodes(tmp_path, [a1, a2])
    candidates = idx.search(MemoryQuery(phrases=("Same",)), limit=10)
    assert len(candidates) == 1


def test_search_phrase_limit_branch(tmp_path):
    many_nodes = [{**_A_NODE, "id": f"n{i}", "source_file": f"00-Inbox/n{i}.md"} for i in range(5)]
    idx = _make_index_with_nodes(tmp_path, many_nodes)
    candidates = idx.search(MemoryQuery(phrases=("Note",)), limit=2)
    assert len(candidates) == 2


def test_search_term_then_phrase_fallback(tmp_path):
    idx = _make_index_with_nodes(tmp_path, [_A_NODE, _B_NODE])
    candidates = idx.search(MemoryQuery(terms=("beta",), phrases=("gamma",)), limit=10)
    assert len(candidates) == 1
    assert candidates[0].path == "10-Projects/beta.md"


# ---------------------------------------------------------------------------
# freshness uses the curated (included_paths) source-set, not a raw vault scan
# ---------------------------------------------------------------------------


def test_excluded_note_change_does_not_mark_index_stale(tmp_path):
    settings = _make_settings(tmp_path)
    vault = settings.vault_root
    policy = MemoryVaultPolicy(("**/*.md",), ("00-Inbox/**",), 10_000, 10_000_000)
    store = ObsidianVaultStore(vault, policy)
    _make_vault_md(vault, "10-Projects/alpha.md")
    (vault / "00-Inbox").mkdir(parents=True, exist_ok=True)
    (vault / "00-Inbox" / "excluded.md").write_text(
        "not part of the include policy", encoding="utf-8"
    )
    source_hash = store.source_snapshot_hash()
    active = _active_dir(settings)
    nodes = [{**_A_NODE, "id": "n1", "source_file": "10-Projects/alpha.md"}]
    _write_graph(active, nodes=nodes, links=[])
    _write_metadata(active, source_snapshot_hash=source_hash, node_count=1)
    idx = GraphifyJsonIndex(settings, store)
    assert idx.status().state is IndexState.FRESH

    # Editing a note OUTSIDE the include policy must not flip freshness --
    # Graphify's source-set and the builder's must agree on what "changed".
    (vault / "00-Inbox" / "excluded.md").write_text("changed content", encoding="utf-8")
    assert idx.status().state is IndexState.FRESH


def test_included_note_change_marks_index_stale(tmp_path):
    settings = _make_settings(tmp_path)
    vault = settings.vault_root
    policy = MemoryVaultPolicy(("**/*.md",), ("00-Inbox/**",), 10_000, 10_000_000)
    store = ObsidianVaultStore(vault, policy)
    _make_vault_md(vault, "10-Projects/alpha.md")
    source_hash = store.source_snapshot_hash()
    active = _active_dir(settings)
    nodes = [{**_A_NODE, "id": "n1", "source_file": "10-Projects/alpha.md"}]
    _write_graph(active, nodes=nodes, links=[])
    _write_metadata(active, source_snapshot_hash=source_hash, node_count=1)
    idx = GraphifyJsonIndex(settings, store)
    assert idx.status().state is IndexState.FRESH

    (vault / "10-Projects" / "alpha.md").write_text("edited content", encoding="utf-8")
    assert idx.status().state is IndexState.STALE
