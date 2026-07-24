"""Workspace path confinement — the single choke point every filesystem and
process tool must pass through.

Strategy: reject absolute paths and any `..` component up front, then fully
resolve symlinks (`Path.resolve`) and require the result to remain inside the
resolved workspace root. String-prefix checking alone is NOT sufficient
(symlinks, `/work` vs `/workspace`); `Path.is_relative_to` on resolved paths
is the containment test.

Known, documented limitation: a symlink introduced between validation and the
subsequent file operation can still redirect the final open (TOCTOU). Phase 11
provides policy enforcement and confinement, not a hardened OS sandbox."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from friday.application.errors import WorkspaceAccessDenied


def resolve_workspace_root(root: Path) -> Path:
    """Validate and canonicalize the configured workspace root."""
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise WorkspaceAccessDenied("workspace root does not exist") from exc
    if not resolved.is_dir():
        raise WorkspaceAccessDenied("workspace root is not a directory")
    return resolved


def resolve_workspace_path(root: Path, requested: str) -> Path:
    """Map an untrusted workspace-relative path onto the real filesystem.

    `root` must already be canonical (see resolve_workspace_root). Raises
    WorkspaceAccessDenied for absolute paths, `..` traversal, NUL bytes, and
    any symlink whose target escapes the workspace."""
    if not requested or "\x00" in requested:
        raise WorkspaceAccessDenied("path is empty or contains NUL")
    relative = PurePosixPath(requested)
    if relative.is_absolute() or requested.startswith("~"):
        raise WorkspaceAccessDenied("absolute paths are not allowed")
    if ".." in relative.parts:
        raise WorkspaceAccessDenied("path traversal ('..') is not allowed")
    resolved = (root / relative).resolve(strict=False)
    if resolved != root and not resolved.is_relative_to(root):
        raise WorkspaceAccessDenied("path escapes the workspace")
    return resolved


def to_workspace_relative(root: Path, resolved: Path) -> str:
    """Render a confined path back as the workspace-relative string used in
    tool outputs and artifact locations."""
    return str(resolved.relative_to(root))
