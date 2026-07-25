"""Memory section rendering remains bounded and cannot displace core context."""

from __future__ import annotations

import pytest

from friday.application.memory.models import (
    IndexState,
    MemoryContext,
    MemoryExcerpt,
    MemoryProvenance,
    RetrievalMethod,
    RetrievalMode,
)
from friday.application.runtime_context import MIN_CONTEXT_CHARS
from tests.application import test_runtime_context as _context_regression_tests
from tests.application.test_runtime_context import _build, _snapshot

# The coverage gate exercises the original deterministic-context contract too.
for _name in dir(_context_regression_tests):
    if _name.startswith("test_"):
        globals()[_name] = getattr(_context_regression_tests, _name)


def _memory(text: str) -> MemoryContext:
    excerpt = MemoryExcerpt("10-Projects/a.md", "A", "Notes", 1, 2, text, "hash", False)
    provenance = MemoryProvenance(
        "10-Projects/a.md",
        "A",
        "Notes",
        1,
        2,
        "hash",
        (RetrievalMethod.LEXICAL_BODY,),
        0,
        None,
        "source",
        False,
    )
    return MemoryContext(
        RetrievalMode.LEXICAL_ONLY, (excerpt,), (provenance,), None, IndexState.MISSING, len(text)
    )


def test_memory_is_rendered_deterministically_with_its_own_bound() -> None:
    snapshot = _snapshot()
    first = _build(snapshot, max_chars=10_000)
    from friday.application.runtime_context import build_runtime_context
    from tests.application.test_runtime_context import MANIFEST

    document = build_runtime_context(
        snapshot,
        tool_manifest=MANIFEST,
        attempt_number=1,
        turn_number=1,
        max_chars=10_000,
        memory_context=_memory("x" * 1_000),
        memory_max_chars=200,
    )
    assert "# MEMORY" in document
    assert "[memory truncated to budget]" in document
    assert document.startswith(first)


def test_memory_never_displaces_required_sections_when_budget_is_tight() -> None:
    from friday.application.runtime_context import build_runtime_context
    from tests.application.test_runtime_context import MANIFEST

    document = build_runtime_context(
        _snapshot(),
        tool_manifest=MANIFEST,
        attempt_number=1,
        turn_number=1,
        max_chars=MIN_CONTEXT_CHARS,
        memory_context=_memory("x" * 1_000),
    )
    assert len(document) <= MIN_CONTEXT_CHARS
    assert "# OBJECTIVE" in document
    assert "# RUN" in document
    assert "# TOOLS" in document


def test_memory_budget_must_be_positive() -> None:
    with pytest.raises(ValueError, match="memory_max_chars"):
        from friday.application.runtime_context import build_runtime_context
        from tests.application.test_runtime_context import MANIFEST

        build_runtime_context(
            _snapshot(),
            tool_manifest=MANIFEST,
            attempt_number=1,
            turn_number=1,
            max_chars=MIN_CONTEXT_CHARS,
            memory_max_chars=0,
        )
