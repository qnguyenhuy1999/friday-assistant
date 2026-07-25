"""Vault path confinement -- the single choke point every vault filesystem
access must pass through.

Strategy: reject absolute paths and any ``..`` component up front, then fully
resolve symlinks (``Path.resolve``) and require the result to remain inside the
resolved vault root. String-prefix checking alone is NOT sufficient (symlinks,
``/vault`` vs ``/vault-old``); ``Path.is_relative_to`` on resolved paths is the
containment test.

Known, documented limitation: a symlink introduced between validation and the
subsequent file operation can still redirect the final open (TOCTOU). This
module provides policy enforcement and confinement, not a hardened OS sandbox.
"""

from __future__ import annotations

import hashlib
import stat
from pathlib import Path, PurePosixPath

from friday.application.memory.errors import MemoryAccessDenied


def resolve_vault_root(root: Path) -> Path:
    """Validate and canonicalize the configured vault root."""
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise MemoryAccessDenied("vault root does not exist") from exc
    if not resolved.is_dir():
        raise MemoryAccessDenied("vault root is not a directory")
    return resolved


def resolve_vault_path(root: Path, requested: str) -> Path:
    """Map an untrusted vault-relative path onto the real filesystem.

    ``root`` must already be canonical (see :func:`resolve_vault_root`). Raises
    MemoryAccessDenied for absolute paths, ``..`` traversal, NUL bytes, and
    any symlink whose target escapes the vault.
    """
    if not requested or "\x00" in requested:
        raise MemoryAccessDenied("path is empty or contains NUL")
    relative = PurePosixPath(requested)
    if relative.is_absolute() or requested.startswith("~"):
        raise MemoryAccessDenied("absolute paths are not allowed")
    if ".." in relative.parts:
        raise MemoryAccessDenied("path traversal ('..') is not allowed")

    candidate = root / relative
    if not is_confined_symlink(root, candidate):
        raise MemoryAccessDenied("path escapes the vault")
    resolved = candidate.resolve(strict=False)
    if resolved != root and not resolved.is_relative_to(root):
        raise MemoryAccessDenied("path escapes the vault")
    return resolved


def to_vault_relative(root: Path, resolved: Path) -> str:
    """Render a confined path as a POSIX-style vault-relative string."""
    return resolved.relative_to(root).as_posix()


def vault_identity_hash(root: Path) -> str:
    """Return the stable identity used to bind derived data to this vault."""
    canonical_root = resolve_vault_root(root)
    return hashlib.sha256(str(canonical_root).encode("utf-8")).hexdigest()


def is_confined_symlink(root: Path, path: Path) -> bool:
    """Return whether each existing symlink in ``path`` stays inside ``root``.

    ``lstat`` deliberately inspects a link's own metadata. This checks both a
    note symlink and every symlinked parent; a missing final path is permitted
    so callers can safely validate intended writes.
    """
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False

    current = root
    for component in relative.parts:
        current = current / component
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        if not stat.S_ISLNK(metadata.st_mode):
            continue
        try:
            target = current.resolve(strict=False)
        except OSError:
            return False
        if target != root and not target.is_relative_to(root):
            return False
    return True
