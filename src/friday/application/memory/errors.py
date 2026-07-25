"""Memory-layer error hierarchy. Extends ApplicationError so callers can
catch memory failures alongside every other application error; see
friday.application.errors for the sibling hierarchy this mirrors."""

from __future__ import annotations

from friday.application.errors import ApplicationError


class MemoryError(ApplicationError):
    """Base class for all memory-layer errors."""


class MemoryAccessDenied(MemoryError):
    """A memory operation attempted to access a path outside the vault's
    granted boundary."""


class MemoryNoteTooLarge(MemoryError):
    """A note exceeds the configured vault policy's max_note_bytes."""


class MemoryIndexUnavailable(MemoryError):
    """The structural index is missing and only lexical retrieval can
    proceed."""


class MemoryIndexCorrupt(MemoryError):
    """The structural index failed validation and must be quarantined."""


class MemoryWriteConflict(MemoryError):
    """A write's observed content hash did not match during its pre-write
    conflict check."""


class MemoryWriteDenied(MemoryError):
    """A write was rejected by policy (disallowed operation, category, or
    path)."""


class MemoryDisabled(MemoryError):
    """Memory retrieval or writes are disabled by configuration."""
