"""Fail-safe graph.json parsing and bounded structural retrieval.

This is the ONLY class that understands Graphify's JSON shape. Raw graph
dictionaries must never cross into the application layer -- map every node
and edge to MemoryCandidate."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from friday.application.memory.models import (
    IndexState,
    IndexStatus,
    MemoryCandidate,
    MemoryQuery,
    RetrievalMethod,
)
from friday.infrastructure.memory.index_metadata import IndexMetadata

_DEFAULT_MAX_GRAPH_BYTES = 100 * 1024 * 1024  # 100 MiB

_KNOWN_RELATIONS: frozenset[str] = frozenset(
    {
        "references",
        "cites",
        "contains",
        "conceptually_related_to",
        "semantically_similar_to",
    }
)

_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {
        "directed",
        "multigraph",
        "graph",
        "nodes",
        "links",
        "hyperedges",
        "built_at_commit",
    }
)

_DOCUMENT_FILE_TYPES: frozenset[str] = frozenset({"document"})

_EXCLUDED_SOURCE_DIRS: frozenset[str] = frozenset(
    {
        ".obsidian",
        ".claude",
        ".git",
        "graphify-out",
        "Attachments",
        "Templates",
        "Archive",
    }
)


@dataclass(frozen=True, slots=True)
class GraphifyJsonIndexSettings:
    vault_root: Path
    index_root: Path
    vault_identity_hash: str
    max_graph_bytes: int = _DEFAULT_MAX_GRAPH_BYTES

    def __post_init__(self) -> None:
        if self.max_graph_bytes <= 0:
            raise ValueError("max_graph_bytes must be positive")


def _vault_source_hash(vault_root: Path) -> str:
    """Deterministic SHA-256 of every .md file in the vault.

    Excludes directories listed in ``_EXCLUDED_SOURCE_DIRS``.  The hash
    covers each file's vault-relative POSIX path and its raw bytes so that
    any rename, deletion, or content change produces a different digest.
    """
    digest = hashlib.sha256()
    try:
        root = vault_root.resolve(strict=False)
    except OSError:
        return digest.hexdigest()
    if not root.is_dir():
        return digest.hexdigest()
    all_md: list[Path] = []
    try:
        for candidate in sorted(root.rglob("*.md"), key=lambda p: p.relative_to(root).as_posix()):
            if not candidate.is_file():
                continue
            try:
                rel = candidate.relative_to(root).as_posix()
            except ValueError:
                continue
            parts = rel.split("/")
            if any(part in _EXCLUDED_SOURCE_DIRS for part in parts):
                continue
            all_md.append(candidate)
    except OSError:
        pass
    for file in all_md:
        try:
            rel = file.relative_to(root).as_posix()
        except ValueError:
            continue
        digest.update(rel.encode("utf-8"))
        digest.update(b"\x00")
        try:
            content = file.read_bytes()
        except OSError:
            continue
        digest.update(content)
        digest.update(b"\x00")
    return digest.hexdigest()


def _check_path_safe(source_file: str) -> str | None:
    """Return a failure code if *source_file* is unsafe, else ``None``."""
    if not source_file:
        return "empty_source_file"
    if source_file.startswith("/") or source_file.startswith("~"):
        return "absolute_source_file"
    if any(part == ".." for part in source_file.split("/")):
        return "path_escapes_vault"
    return None


def _check_graph_shape(data: dict[str, Any]) -> str | None:
    """Validate graph.json node/link shape.  Returns a failure code or ``None``."""
    nodes = data.get("nodes")
    if not isinstance(nodes, list):
        return "nodes_not_a_list"
    links = data.get("links")
    if not isinstance(links, list):
        return "links_not_a_list"
    seen_ids: set[str] = set()
    for node in nodes:
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            return "missing_or_invalid_node_id"
        if node_id in seen_ids:
            return "duplicate_node_id"
        seen_ids.add(node_id)
        source_file = node.get("source_file")
        if source_file is not None and not isinstance(source_file, str):
            return "non_string_source_file"
        if isinstance(source_file, str):
            err = _check_path_safe(source_file)
            if err is not None:
                return err
    for link in links:
        source = link.get("source")
        target = link.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            return "missing_edge_source_or_target"
        if source not in seen_ids:
            return "dangling_edge_source"
        if target not in seen_ids:
            return "dangling_edge_target"
    return None


def _node_source_file(node: dict[str, Any]) -> str | None:
    sf = node.get("source_file")
    return sf if isinstance(sf, str) else None


def _is_document_node(node: dict[str, Any]) -> bool:
    return node.get("file_type") in _DOCUMENT_FILE_TYPES


def _is_markdown(source_file: str) -> bool:
    return source_file.endswith(".md")


class GraphifyJsonIndex:
    """``StructuralIndex`` that parses a prebuilt ``graph.json`` on every call.

    Every public method is fail-safe: if the file is missing, oversized,
    corrupt, or points at a stale vault snapshot the result reflects the
    problem through ``IndexStatus`` (for ``status()``) or an empty tuple
    (for ``search()`` / ``neighbors()``).  No exception escapes into a
    ``Run`` from structural retrieval.
    """

    def __init__(self, settings: GraphifyJsonIndexSettings) -> None:
        self._settings = settings

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _active_dir(self) -> Path:
        slug = self._settings.vault_identity_hash[:32]
        return self._settings.index_root / slug / "active"

    def _graph_path(self) -> Path:
        return self._active_dir() / "graph.json"

    def _try_load_graph(self) -> tuple[dict[str, Any] | None, str | None]:
        """Read, size-check, parse JSON, and shape-check graph.json.

        Returns ``(parsed_dict, None)`` on success or ``(None, failure_code)``
        on any failure.
        """
        graph_path = self._graph_path()
        if not graph_path.is_file():
            return None, "index_missing"
        try:
            if graph_path.stat().st_size > self._settings.max_graph_bytes:
                return None, "oversized_graph"
            raw = graph_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None, "invalid_utf8"
        except OSError:
            return None, "unreadable_graph"
        try:
            data: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            return None, "invalid_json"
        if not isinstance(data, dict):
            return None, "not_an_object"
        if set(data) != _TOP_LEVEL_KEYS:
            return None, "invalid_top_level_keys"
        error = _check_graph_shape(data)
        if error is not None:
            return None, error
        return data, None

    def _get_applicable_nodes(self) -> tuple[list[dict[str, Any]], str | None]:
        """Return document-type .md nodes from the graph, or a failure code."""
        data, failure = self._try_load_graph()
        if failure is not None or data is None:
            return [], failure
        result: list[dict[str, Any]] = []
        for node in data["nodes"]:
            if not _is_document_node(node):
                continue
            sf = _node_source_file(node)
            if sf is None or not _is_markdown(sf):
                continue
            result.append(node)
        return result, None

    # ------------------------------------------------------------------
    # StructuralIndex protocol
    # ------------------------------------------------------------------

    def status(self) -> IndexStatus:
        active = self._active_dir()
        graph_path = self._graph_path()

        if not active.is_dir() or not graph_path.is_file():
            return IndexStatus(
                state=IndexState.MISSING,
                snapshot_id=None,
                source_snapshot_hash=None,
                graph_checksum=None,
                node_count=0,
                edge_count=0,
                built_at=None,
                failure_code="index_missing",
            )

        # Size check before any read
        try:
            file_size = graph_path.stat().st_size
        except OSError:
            return IndexStatus(
                state=IndexState.CORRUPT,
                snapshot_id=None,
                source_snapshot_hash=None,
                graph_checksum=None,
                node_count=0,
                edge_count=0,
                built_at=None,
                failure_code="unreadable_graph",
            )
        if file_size > self._settings.max_graph_bytes:
            return IndexStatus(
                state=IndexState.CORRUPT,
                snapshot_id=None,
                source_snapshot_hash=None,
                graph_checksum=None,
                node_count=0,
                edge_count=0,
                built_at=None,
                failure_code="oversized_graph",
            )

        # Metadata
        try:
            metadata = IndexMetadata.read(active)
        except ValueError:
            return IndexStatus(
                state=IndexState.CORRUPT,
                snapshot_id=None,
                source_snapshot_hash=None,
                graph_checksum=None,
                node_count=0,
                edge_count=0,
                built_at=None,
                failure_code="invalid_metadata",
            )

        # Graph content validation
        data, failure = self._try_load_graph()
        if failure is not None:
            return IndexStatus(
                state=IndexState.CORRUPT,
                snapshot_id=None,
                source_snapshot_hash=metadata.source_snapshot_hash,
                graph_checksum=metadata.graph_checksum,
                node_count=0,
                edge_count=0,
                built_at=metadata.built_at,
                failure_code=failure,
            )

        # Freshness
        current_hash = _vault_source_hash(self._settings.vault_root)
        state = (
            IndexState.FRESH if current_hash == metadata.source_snapshot_hash else IndexState.STALE
        )

        return IndexStatus(
            state=state,
            snapshot_id=metadata.graph_checksum,
            source_snapshot_hash=metadata.source_snapshot_hash,
            graph_checksum=metadata.graph_checksum,
            node_count=metadata.node_count,
            edge_count=metadata.edge_count,
            built_at=metadata.built_at,
            failure_code=None,
        )

    def search(self, query: MemoryQuery, *, limit: int) -> tuple[MemoryCandidate, ...]:
        applicable, failure = self._get_applicable_nodes()
        if failure is not None or not applicable:
            return ()

        seen: set[str] = set()
        candidates: list[MemoryCandidate] = []

        # Title / norm_label lookup (exact, case-insensitive)
        for title in query.titles:
            title_lower = title.casefold()
            for node in applicable:
                sf = _node_source_file(node) or ""
                if sf in seen:
                    continue
                label = node.get("label", "")
                norm = node.get("norm_label", "")
                label_match = isinstance(label, str) and label.casefold() == title_lower
                norm_match = isinstance(norm, str) and norm.casefold() == title_lower
                if not label_match and not norm_match:
                    continue
                seen.add(sf)
                candidates.append(
                    MemoryCandidate(
                        path=sf,
                        title=str(label),
                        methods=(RetrievalMethod.STRUCTURAL_NODE,),
                        score=1.0,
                    )
                )
                if len(candidates) >= limit:
                    break
            if len(candidates) >= limit:
                break

        # Term match (substring, case-insensitive)
        if len(candidates) < limit:
            for term in query.terms:
                term_lower = term.casefold()
                for node in applicable:
                    sf = _node_source_file(node) or ""
                    if sf in seen:
                        continue
                    label = node.get("label", "")
                    if not isinstance(label, str) or term_lower not in label.casefold():
                        continue
                    seen.add(sf)
                    candidates.append(
                        MemoryCandidate(
                            path=sf,
                            title=label,
                            methods=(RetrievalMethod.STRUCTURAL_NODE,),
                            score=0.9,
                        )
                    )
                    if len(candidates) >= limit:
                        break
                if len(candidates) >= limit:
                    break

        # Phrase match (substring, case-insensitive, same logic as term)
        if len(candidates) < limit:
            for phrase in query.phrases:
                phrase_lower = phrase.casefold()
                for node in applicable:
                    sf = _node_source_file(node) or ""
                    if sf in seen:
                        continue
                    label = node.get("label", "")
                    if not isinstance(label, str) or phrase_lower not in label.casefold():
                        continue
                    seen.add(sf)
                    candidates.append(
                        MemoryCandidate(
                            path=sf,
                            title=label,
                            methods=(RetrievalMethod.STRUCTURAL_NODE,),
                            score=0.9,
                        )
                    )
                    if len(candidates) >= limit:
                        break
                if len(candidates) >= limit:
                    break

        return tuple(candidates[:limit])

    def neighbors(self, path: str, *, depth: int, max_nodes: int) -> tuple[MemoryCandidate, ...]:
        data, failure = self._try_load_graph()
        if failure is not None or data is None:
            return ()

        nodes = data["nodes"]
        links = data["links"]

        nodes_by_id: dict[str, dict[str, Any]] = {}
        nodes_by_path: dict[str, dict[str, Any]] = {}
        for n in nodes:
            nid: str = n["id"]
            nodes_by_id[nid] = n
            sf = _node_source_file(n)
            if sf is not None:
                nodes_by_path[sf] = n

        start_node = nodes_by_path.get(path)
        if start_node is None:
            return ()
        start_id: str = start_node["id"]

        # Bidirectional adjacency over _KNOWN_RELATIONS only
        adj: dict[str, list[tuple[str, bool]]] = {}
        for link in links:
            rel = link.get("relation", "")
            if rel not in _KNOWN_RELATIONS:
                continue
            src: str = link["source"]
            tgt: str = link["target"]
            if src not in nodes_by_id or tgt not in nodes_by_id:
                continue
            adj.setdefault(src, []).append((tgt, False))
            adj.setdefault(tgt, []).append((src, True))

        visited: dict[str, int] = {start_id: 0}
        queue: deque[tuple[str, int]] = deque([(start_id, 0)])
        result: list[MemoryCandidate] = []

        while queue and len(result) < max_nodes:
            current_id, dist = queue.popleft()
            if current_id != start_id:
                current_node = nodes_by_id[current_id]
                sf = _node_source_file(current_node)
                if sf is not None and _is_document_node(current_node) and _is_markdown(sf):
                    result.append(
                        MemoryCandidate(
                            path=sf,
                            title=str(current_node.get("label", "")),
                            methods=(RetrievalMethod.STRUCTURAL_NEIGHBOR,),
                            score=1.0,
                            graph_distance=dist,
                        )
                    )
                if len(result) >= max_nodes:
                    break
            if dist >= depth:
                continue
            for neighbor_id, _ in adj.get(current_id, []):
                if neighbor_id not in visited:
                    visited[neighbor_id] = dist + 1
                    queue.append((neighbor_id, dist + 1))

        # Deterministic sort: (distance, path)
        result.sort(key=lambda c: (c.graph_distance or 0, c.path))
        return tuple(result[:max_nodes])
