"""Phase 12 memory settings: vault retrieval and derived Graphify index limits.

Deliberately separate from WorkerSettings and RuntimeSettings — one module per
concern.  ``max_total_context_chars`` is capped at RuntimeSettings' default
context budget, leaving room for the active task and system instructions.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Final

from apps.worker.runtime_settings import _DEFAULT_MAX_CONTEXT_CHARS

_DEFAULT_MEMORY_ENABLED = False
_DEFAULT_OBSIDIAN_VAULT_ROOT = "~/Documents/secondbrain"
_DEFAULT_OBSIDIAN_MANAGED_ROOT = "Friday"
_DEFAULT_MEMORY_INCLUDE_GLOBS: Final[tuple[str, ...]] = ()
_DEFAULT_MEMORY_EXCLUDE_GLOBS: Final[tuple[str, ...]] = (
    ".obsidian/**",
    ".trash/**",
    ".claude/**",
    ".git/**",
    "**/.DS_Store",
    "**/.git/**",
    "**/node_modules/**",
    "graphify-out/**",
    "Attachments/**",
    "Templates/**",
    "**/*.canvas",
    "**/*.pdf",
    "**/*.png",
    "**/*.jpg",
    "**/*.jpeg",
    "**/*.gif",
    "**/*.mov",
    "**/*.mp4",
    "**/*.zip",
)
_DEFAULT_MEMORY_MAX_FILES = 5_000
_DEFAULT_MEMORY_MAX_NOTE_BYTES = 1_000_000
_DEFAULT_MEMORY_MAX_CANDIDATES = 50
_DEFAULT_MEMORY_MAX_EXCERPTS = 6
_DEFAULT_MEMORY_MAX_EXCERPT_CHARS = 2_000
_DEFAULT_MEMORY_MAX_TOTAL_CONTEXT_CHARS = 12_000
_DEFAULT_MEMORY_MAX_GRAPH_DEPTH = 2
_DEFAULT_MEMORY_MAX_GRAPH_NODES_VISITED = 500
_DEFAULT_GRAPHIFY_ENABLED = False
_DEFAULT_GRAPHIFY_EXECUTABLE = "graphify"
_DEFAULT_GRAPHIFY_INDEX_ROOT = "~/.friday/graphify"
_DEFAULT_GRAPHIFY_BUILD_TIMEOUT_SECONDS = 900.0
_DEFAULT_GRAPHIFY_MAX_STDOUT_BYTES = 200_000
_DEFAULT_GRAPHIFY_MAX_STDERR_BYTES = 200_000
_DEFAULT_GRAPHIFY_MAX_GRAPH_BYTES = 50_000_000
_DEFAULT_MEMORY_INDEX_MAINTENANCE_SECONDS = 900.0
_DEFAULT_MEMORY_INDEX_MAX_FILES_PER_SCAN = 5_000


def _parse_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _parse_globs(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _expand_path(value: str) -> Path:
    return Path(value).expanduser().resolve(strict=False)


@dataclass(frozen=True, slots=True)
class MemorySettings:
    memory_enabled: bool
    vault_root: Path
    managed_root: str
    include_globs: tuple[str, ...]
    exclude_globs: tuple[str, ...]
    max_files: int
    max_note_bytes: int
    max_candidates: int
    max_excerpts: int
    max_excerpt_chars: int
    max_total_context_chars: int
    max_graph_depth: int
    max_graph_nodes_visited: int
    graphify_enabled: bool
    graphify_executable: str
    graphify_index_root: Path
    graphify_build_timeout_seconds: float
    graphify_max_stdout_bytes: int
    graphify_max_stderr_bytes: int
    graphify_max_graph_bytes: int
    index_maintenance_seconds: float
    index_max_files_per_scan: int

    @property
    def effective_include_globs(self) -> tuple[str, ...]:
        """The configured managed area is Friday-owned memory and must be
        retrievable; keep it in the same curated source set as all reads."""
        managed = f"{self.managed_root.rstrip('/')}/*.md"
        recursive_managed = f"{self.managed_root.rstrip('/')}/**/*.md"
        return tuple(dict.fromkeys((*self.include_globs, managed, recursive_managed)))

    def __post_init__(self) -> None:
        positives = {
            "max_files": self.max_files,
            "max_note_bytes": self.max_note_bytes,
            "max_candidates": self.max_candidates,
            "max_excerpts": self.max_excerpts,
            "max_excerpt_chars": self.max_excerpt_chars,
            "max_total_context_chars": self.max_total_context_chars,
            "max_graph_depth": self.max_graph_depth,
            "max_graph_nodes_visited": self.max_graph_nodes_visited,
            "graphify_build_timeout_seconds": self.graphify_build_timeout_seconds,
            "graphify_max_stdout_bytes": self.graphify_max_stdout_bytes,
            "graphify_max_stderr_bytes": self.graphify_max_stderr_bytes,
            "graphify_max_graph_bytes": self.graphify_max_graph_bytes,
            "index_maintenance_seconds": self.index_maintenance_seconds,
            "index_max_files_per_scan": self.index_max_files_per_scan,
        }
        for name, value in positives.items():
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if self.max_total_context_chars > _DEFAULT_MAX_CONTEXT_CHARS:
            raise ValueError("max_total_context_chars exceeds the RuntimeSettings default")
        if self.max_excerpt_chars * self.max_excerpts > self.max_total_context_chars:
            raise ValueError("excerpt budget exceeds max_total_context_chars")
        if self.graphify_enabled and not self.graphify_executable.strip():
            raise ValueError("graphify_executable must not be empty when graphify is enabled")
        if not self.memory_enabled:
            return
        if not self.include_globs:
            raise ValueError("include_globs must not be empty when memory is enabled")
        try:
            vault_root = self.vault_root.resolve(strict=True)
        except FileNotFoundError as error:
            raise ValueError("vault_root must exist when memory is enabled") from error
        if not vault_root.is_dir():
            raise ValueError("vault_root must be a directory when memory is enabled")
        managed_path = Path(self.managed_root)
        if (
            not self.managed_root.strip()
            or managed_path.is_absolute()
            or ".." in managed_path.parts
        ):
            raise ValueError("managed_root must be vault-relative and must not escape the vault")
        index_root = self.graphify_index_root.resolve(strict=False)
        if index_root.is_relative_to(vault_root):
            raise ValueError("graphify_index_root must be outside vault_root")

    @classmethod
    def from_env(cls) -> MemorySettings:
        return cls(
            memory_enabled=_parse_bool("FRIDAY_MEMORY_ENABLED", _DEFAULT_MEMORY_ENABLED),
            vault_root=_expand_path(
                os.environ.get("FRIDAY_OBSIDIAN_VAULT_ROOT", _DEFAULT_OBSIDIAN_VAULT_ROOT)
            ),
            managed_root=os.environ.get(
                "FRIDAY_OBSIDIAN_MANAGED_ROOT", _DEFAULT_OBSIDIAN_MANAGED_ROOT
            ),
            include_globs=_parse_globs(os.environ.get("FRIDAY_MEMORY_INCLUDE_GLOBS", "")),
            exclude_globs=_parse_globs(
                os.environ.get(
                    "FRIDAY_MEMORY_EXCLUDE_GLOBS", ",".join(_DEFAULT_MEMORY_EXCLUDE_GLOBS)
                )
            ),
            max_files=int(os.environ.get("FRIDAY_MEMORY_MAX_FILES", _DEFAULT_MEMORY_MAX_FILES)),
            max_note_bytes=int(
                os.environ.get("FRIDAY_MEMORY_MAX_NOTE_BYTES", _DEFAULT_MEMORY_MAX_NOTE_BYTES)
            ),
            max_candidates=int(
                os.environ.get("FRIDAY_MEMORY_MAX_CANDIDATES", _DEFAULT_MEMORY_MAX_CANDIDATES)
            ),
            max_excerpts=int(
                os.environ.get("FRIDAY_MEMORY_MAX_EXCERPTS", _DEFAULT_MEMORY_MAX_EXCERPTS)
            ),
            max_excerpt_chars=int(
                os.environ.get("FRIDAY_MEMORY_MAX_EXCERPT_CHARS", _DEFAULT_MEMORY_MAX_EXCERPT_CHARS)
            ),
            max_total_context_chars=int(
                os.environ.get(
                    "FRIDAY_MEMORY_MAX_TOTAL_CONTEXT_CHARS", _DEFAULT_MEMORY_MAX_TOTAL_CONTEXT_CHARS
                )
            ),
            max_graph_depth=int(
                os.environ.get("FRIDAY_MEMORY_MAX_GRAPH_DEPTH", _DEFAULT_MEMORY_MAX_GRAPH_DEPTH)
            ),
            max_graph_nodes_visited=int(
                os.environ.get(
                    "FRIDAY_MEMORY_MAX_GRAPH_NODES_VISITED", _DEFAULT_MEMORY_MAX_GRAPH_NODES_VISITED
                )
            ),
            graphify_enabled=_parse_bool("FRIDAY_GRAPHIFY_ENABLED", _DEFAULT_GRAPHIFY_ENABLED),
            graphify_executable=os.environ.get(
                "FRIDAY_GRAPHIFY_EXECUTABLE", _DEFAULT_GRAPHIFY_EXECUTABLE
            ),
            graphify_index_root=_expand_path(
                os.environ.get("FRIDAY_GRAPHIFY_INDEX_ROOT", _DEFAULT_GRAPHIFY_INDEX_ROOT)
            ),
            graphify_build_timeout_seconds=float(
                os.environ.get(
                    "FRIDAY_GRAPHIFY_BUILD_TIMEOUT_SECONDS", _DEFAULT_GRAPHIFY_BUILD_TIMEOUT_SECONDS
                )
            ),
            graphify_max_stdout_bytes=int(
                os.environ.get(
                    "FRIDAY_GRAPHIFY_MAX_STDOUT_BYTES", _DEFAULT_GRAPHIFY_MAX_STDOUT_BYTES
                )
            ),
            graphify_max_stderr_bytes=int(
                os.environ.get(
                    "FRIDAY_GRAPHIFY_MAX_STDERR_BYTES", _DEFAULT_GRAPHIFY_MAX_STDERR_BYTES
                )
            ),
            graphify_max_graph_bytes=int(
                os.environ.get("FRIDAY_GRAPHIFY_MAX_GRAPH_BYTES", _DEFAULT_GRAPHIFY_MAX_GRAPH_BYTES)
            ),
            index_maintenance_seconds=float(
                os.environ.get(
                    "FRIDAY_MEMORY_INDEX_MAINTENANCE_SECONDS",
                    _DEFAULT_MEMORY_INDEX_MAINTENANCE_SECONDS,
                )
            ),
            index_max_files_per_scan=int(
                os.environ.get(
                    "FRIDAY_MEMORY_INDEX_MAX_FILES_PER_SCAN",
                    _DEFAULT_MEMORY_INDEX_MAX_FILES_PER_SCAN,
                )
            ),
        )
