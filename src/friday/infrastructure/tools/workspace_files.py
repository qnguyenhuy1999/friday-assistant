"""Workspace filesystem tools: list, read_text, write_text.

Every path passes through workspace_paths confinement. Input problems raise
ToolInputInvalid / WorkspaceAccessDenied (the gateway maps them to stable
failure codes); runtime outcomes (missing file, truncation) come back as
structured results. Writes are atomic (temp file + os.replace) and return an
ArtifactCandidate with a SHA-256 checksum."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from friday.application.errors import ToolInputInvalid
from friday.application.tool_gateway import ArtifactCandidate, ToolExecutionResult
from friday.domain.artifact import ArtifactKind
from friday.domain.failure import Failure, FailureCause
from friday.domain.json_value import JsonValue
from friday.infrastructure.tools.workspace_paths import (
    resolve_workspace_path,
    to_workspace_relative,
)

_TEXT_TRUNCATION_MARKER = "\n…[truncated: file exceeds read limit]"


@dataclass(frozen=True, slots=True)
class WorkspaceFileSettings:
    root: Path
    max_file_bytes: int
    max_list_entries: int

    def __post_init__(self) -> None:
        if self.max_file_bytes <= 0:
            raise ValueError("max_file_bytes must be positive")
        if self.max_list_entries <= 0:
            raise ValueError("max_list_entries must be positive")


def _payload(tool_input: JsonValue, allowed: set[str], required: set[str]) -> dict[str, JsonValue]:
    if not isinstance(tool_input, dict):
        raise ToolInputInvalid("tool input must be a JSON object")
    unknown = set(tool_input) - allowed
    if unknown:
        raise ToolInputInvalid(f"unknown input field(s): {sorted(unknown)}")
    missing = required - set(tool_input)
    if missing:
        raise ToolInputInvalid(f"missing required input field(s): {sorted(missing)}")
    return tool_input


def _require_str(payload: dict[str, JsonValue], field: str, default: str | None = None) -> str:
    value = payload.get(field, default)
    if not isinstance(value, str) or not value:
        raise ToolInputInvalid(f"'{field}' must be a non-empty string")
    return value


def _require_bool(payload: dict[str, JsonValue], field: str, default: bool) -> bool:
    value = payload.get(field, default)
    if not isinstance(value, bool):
        raise ToolInputInvalid(f"'{field}' must be a boolean")
    return value


def _not_found(path: str) -> ToolExecutionResult:
    return ToolExecutionResult.failed(
        Failure(
            code="tool_execution_failed",
            message=f"path does not exist: {path}",
            retryable=False,
            cause=FailureCause.TOOL,
        )
    )


class WorkspaceFiles:
    def __init__(self, settings: WorkspaceFileSettings) -> None:
        self._settings = settings

    def list_entries(self, tool_input: JsonValue) -> ToolExecutionResult:
        payload = _payload(tool_input, {"path", "recursive"}, set())
        requested = _require_str(payload, "path", default=".")
        recursive = _require_bool(payload, "recursive", default=False)
        root = self._settings.root
        directory = resolve_workspace_path(root, requested)
        if not directory.exists():
            return _not_found(requested)
        if not directory.is_dir():
            raise ToolInputInvalid(f"not a directory: {requested}")

        entries: list[JsonValue] = []
        truncated = False
        for path in _iter_entries(directory, recursive):
            if len(entries) >= self._settings.max_list_entries:
                truncated = True
                break
            entries.append(
                {
                    "path": to_workspace_relative(root, path),
                    "type": "dir" if path.is_dir() else "file",
                    "size": path.stat().st_size if path.is_file() else None,
                }
            )
        return ToolExecutionResult.succeeded({"entries": entries, "truncated": truncated})

    def read_text(self, tool_input: JsonValue) -> ToolExecutionResult:
        payload = _payload(tool_input, {"path"}, {"path"})
        requested = _require_str(payload, "path")
        target = resolve_workspace_path(self._settings.root, requested)
        if not target.exists():
            return _not_found(requested)
        if not target.is_file():
            raise ToolInputInvalid(f"not a regular file: {requested}")

        size = target.stat().st_size
        limit = self._settings.max_file_bytes
        with target.open("rb") as handle:
            raw = handle.read(limit)
        if b"\x00" in raw:
            raise ToolInputInvalid(f"not a UTF-8 text file: {requested}")
        truncated = size > limit
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            if not truncated:
                raise ToolInputInvalid(f"not a UTF-8 text file: {requested}") from None
            # a multibyte character was cut at the read limit — drop the
            # partial tail deterministically
            content = raw.decode("utf-8", errors="ignore")
        if truncated:
            content += _TEXT_TRUNCATION_MARKER
        return ToolExecutionResult.succeeded(
            {"content": content, "size": size, "truncated": truncated}
        )

    def write_text(self, tool_input: JsonValue) -> ToolExecutionResult:
        payload = _payload(
            tool_input, {"path", "content", "overwrite", "create_parents"}, {"path", "content"}
        )
        requested = _require_str(payload, "path")
        content = payload.get("content")
        if not isinstance(content, str):
            raise ToolInputInvalid("'content' must be a string")
        overwrite = _require_bool(payload, "overwrite", default=False)
        create_parents = _require_bool(payload, "create_parents", default=False)

        encoded = content.encode("utf-8")
        if len(encoded) > self._settings.max_file_bytes:
            raise ToolInputInvalid("content exceeds the configured file size limit")

        root = self._settings.root
        target = resolve_workspace_path(root, requested)
        if target == root or target.is_dir():
            raise ToolInputInvalid(f"target is a directory: {requested}")
        existed = target.exists()
        if existed and not overwrite:
            raise ToolInputInvalid(f"file exists and overwrite is false: {requested}")
        parent = target.parent
        if not parent.exists():
            if not create_parents:
                raise ToolInputInvalid(f"parent directory does not exist: {requested}")
            parent.mkdir(parents=True, exist_ok=True)

        # atomic replacement: write a sibling temp file, then os.replace
        descriptor, temp_name = tempfile.mkstemp(dir=parent, prefix=".friday-write-")
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
            os.replace(temp_name, target)
        except OSError:
            os.unlink(temp_name)
            raise

        checksum = hashlib.sha256(encoded).hexdigest()
        location = to_workspace_relative(root, target)
        candidate = ArtifactCandidate(
            kind=ArtifactKind.FILE,
            name=target.name,
            media_type=mimetypes.guess_type(target.name)[0] or "text/plain",
            location=location,
            size=len(encoded),
            checksum=checksum,
        )
        return ToolExecutionResult.succeeded(
            {
                "path": location,
                "size": len(encoded),
                "checksum": checksum,
                "created": not existed,
            },
            artifacts=(candidate,),
        )


def _iter_entries(directory: Path, recursive: bool) -> list[Path]:
    """Deterministic listing: lexicographic by workspace-relative path.
    Recursion is shallow — at most one directory level below `directory`."""
    children = sorted(directory.iterdir(), key=lambda p: p.name)
    if not recursive:
        return children
    result: list[Path] = []
    for child in children:
        result.append(child)
        if child.is_dir() and not child.is_symlink():
            result.extend(sorted(child.iterdir(), key=lambda p: p.name))
    return result
