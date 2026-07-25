"""Narrow, bounded ToolGateway handlers for curated Obsidian memory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from friday.application.errors import ToolInputInvalid
from friday.application.memory.errors import (
    MemoryAccessDenied,
    MemoryWriteConflict,
    MemoryWriteDenied,
)
from friday.application.memory.models import (
    MemoryCandidate,
    MemoryQuery,
    MemoryVaultPolicy,
    MemoryWriteCandidate,
    MemoryWriteOperation,
    RetrievalMethod,
)
from friday.application.memory.write_policy import MemoryWritePolicy
from friday.application.tool_gateway import ToolExecutionResult
from friday.domain.failure import Failure, FailureCause
from friday.domain.json_value import JsonValue
from friday.infrastructure.memory.obsidian_vault import ObsidianVaultStore

_DEFAULT_SEARCH_LIMIT = 10
_DEFAULT_MAX_EXCERPT_CHARS = 2_000


@dataclass(frozen=True, slots=True)
class MemoryToolSettings:
    vault_root: Path
    policy: MemoryVaultPolicy
    managed_root: str = "Friday"
    max_search_limit: int = _DEFAULT_SEARCH_LIMIT
    max_excerpt_chars: int = _DEFAULT_MAX_EXCERPT_CHARS

    def __post_init__(self) -> None:
        if self.max_search_limit <= 0:
            raise ValueError("MemoryToolSettings.max_search_limit must be positive")
        if self.max_excerpt_chars <= 0:
            raise ValueError("MemoryToolSettings.max_excerpt_chars must be positive")


class MemoryTools:
    """Tool handlers with no path other than the configured vault store."""

    def __init__(self, settings: MemoryToolSettings) -> None:
        self._settings = settings
        self._store = ObsidianVaultStore(
            settings.vault_root, settings.policy, managed_root=settings.managed_root
        )
        self._write_policy = MemoryWritePolicy(settings.managed_root)

    def search(self, tool_input: JsonValue) -> ToolExecutionResult:
        values = _object(tool_input)
        query = _string(values, "query")
        limit = _bounded_integer(values, "limit", self._settings.max_search_limit)
        candidates = self._store.search_lexical(MemoryQuery(terms=(query,)), limit=limit)
        items: list[JsonValue] = [self._excerpt_item(candidate) for candidate in candidates]
        return ToolExecutionResult.succeeded({"results": items})

    def read_note(self, tool_input: JsonValue) -> ToolExecutionResult:
        values = _object(tool_input)
        path = _string(values, "path")
        heading = _optional_string(values, "heading")
        max_chars = _bounded_integer(values, "max_chars", self._settings.max_excerpt_chars)
        if path not in self._store.included_paths():
            raise MemoryAccessDenied("note is excluded, sensitive, or unavailable")
        candidate = MemoryCandidate(
            path,
            Path(path).stem,
            methods=(_lexical_method(),),
            score=1.0,
            headings=(heading,) if heading is not None else (),
        )
        excerpt = self._store.read_excerpt(candidate, max_chars=max_chars)
        return ToolExecutionResult.succeeded(
            {
                "path": excerpt.path,
                "heading": excerpt.heading,
                "content": excerpt.text,
                "truncated": excerpt.truncated,
                "provenance": {
                    "path": excerpt.path,
                    "start_line": excerpt.start_line,
                    "end_line": excerpt.end_line,
                    "content_hash": excerpt.content_hash,
                },
            }
        )

    def create_note(self, tool_input: JsonValue) -> ToolExecutionResult:
        candidate = self._write_candidate(_object(tool_input), MemoryWriteOperation.CREATE_NOTE)
        return self._write(candidate)

    def append_managed_note(self, tool_input: JsonValue) -> ToolExecutionResult:
        candidate = self._write_candidate(
            _object(tool_input), MemoryWriteOperation.APPEND_MANAGED_NOTE
        )
        return self._write(candidate)

    def _excerpt_item(self, candidate: MemoryCandidate) -> dict[str, JsonValue]:
        excerpt = self._store.read_excerpt(candidate, max_chars=self._settings.max_excerpt_chars)
        return {
            "path": excerpt.path,
            "excerpt": excerpt.text,
            "truncated": excerpt.truncated,
            "provenance": {
                "path": excerpt.path,
                "start_line": excerpt.start_line,
                "end_line": excerpt.end_line,
                "content_hash": excerpt.content_hash,
                "methods": [method.value for method in candidate.methods],
            },
        }

    def _write_candidate(
        self, values: dict[str, JsonValue], operation: MemoryWriteOperation
    ) -> MemoryWriteCandidate:
        frontmatter = _frontmatter(values) if operation is MemoryWriteOperation.CREATE_NOTE else ()
        candidate = MemoryWriteCandidate(
            operation=operation,
            path=_string(values, "path"),
            observed_content_hash=(
                None
                if operation is MemoryWriteOperation.CREATE_NOTE
                else _string(values, "observed_content_hash")
            ),
            payload=_string(values, "payload"),
            frontmatter=frontmatter,
            memory_category=_string(values, "memory_category"),
        )
        return self._write_policy.validate(candidate).candidate

    def _write(self, candidate: MemoryWriteCandidate) -> ToolExecutionResult:
        result = self._store.write_candidate(candidate)
        return ToolExecutionResult.succeeded(
            {"path": result.path, "content_hash": result.content_hash, "created": result.created}
        )


def memory_failure(exc: Exception) -> ToolExecutionResult:
    if isinstance(exc, MemoryWriteConflict):
        code, message, cause = "memory_write_conflict", "memory note changed", FailureCause.TOOL
    elif isinstance(exc, (MemoryAccessDenied, MemoryWriteDenied)):
        code, message, cause = (
            "memory_access_denied",
            "memory access denied",
            FailureCause.VALIDATION,
        )
    elif isinstance(exc, ToolInputInvalid):
        code, message, cause = "tool_invalid_input", str(exc), FailureCause.VALIDATION
    else:
        code, message, cause = (
            "tool_execution_failed",
            "memory tool execution failed",
            FailureCause.TOOL,
        )
    return ToolExecutionResult.failed(
        Failure(code=code, message=message, retryable=False, cause=cause)
    )


def _object(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ToolInputInvalid("tool input must be an object")
    return value


def _string(values: dict[str, JsonValue], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ToolInputInvalid(f"{name} must be a non-empty string")
    return value


def _optional_string(values: dict[str, JsonValue], name: str) -> str | None:
    value = values.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ToolInputInvalid(f"{name} must be a non-empty string when supplied")
    return value


def _bounded_integer(values: dict[str, JsonValue], name: str, maximum: int) -> int:
    value = values.get(name, maximum)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > maximum:
        raise ToolInputInvalid(f"{name} must be an integer between 1 and {maximum}")
    return value


def _frontmatter(values: dict[str, JsonValue]) -> tuple[tuple[str, str], ...]:
    raw = values.get("frontmatter")
    if not isinstance(raw, dict):
        raise ToolInputInvalid("frontmatter must be an object")
    result: list[tuple[str, str]] = []
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ToolInputInvalid("frontmatter keys and values must be strings")
        result.append((key, value))
    return tuple(result)


def _lexical_method() -> RetrievalMethod:
    return RetrievalMethod.LEXICAL_BODY
