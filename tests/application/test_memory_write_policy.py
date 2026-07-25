"""Policy tests for curated Friday memory mutations."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from friday.application.memory.errors import MemoryWriteDenied
from friday.application.memory.models import MemoryWriteCandidate, MemoryWriteOperation
from friday.application.memory.write_policy import (
    MemoryCategory,
    MemoryWritePolicy,
    _has_high_entropy_token,
)
from friday.domain.identifiers import RunId, RunStepId

RUN_ID = RunId.parse("22222222-2222-2222-2222-222222222222")
STEP_ID = RunStepId.parse("33333333-3333-3333-3333-333333333333")
POLICY = MemoryWritePolicy()
FRONTMATTER = (
    ("friday_managed", "true"),
    ("friday_memory_id", "memory-1"),
    ("source_run_id", str(RUN_ID)),
    ("created_at", "2026-01-01T00:00:00Z"),
    ("updated_at", "2026-01-01T00:00:00Z"),
)


def _candidate(**changes: object) -> MemoryWriteCandidate:
    values: dict[str, object] = {
        "operation": MemoryWriteOperation.CREATE_NOTE,
        "path": "Friday/Inbox/example.md",
        "observed_content_hash": None,
        "payload": "A durable decision.",
        "frontmatter": FRONTMATTER,
        "memory_category": MemoryCategory.EXPLICIT_DECISION,
    }
    values.update(changes)
    return MemoryWriteCandidate(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("category", tuple(MemoryCategory))
def test_eligible_category_is_validated(category: MemoryCategory) -> None:
    assert POLICY.validate(_candidate(memory_category=category)).approval_required


@pytest.mark.parametrize(
    "category",
    (
        "raw_conversation_transcript",
        "temporary_status",
        "speculative_inference",
        "full_tool_output",
        "secrets",
        "credentials",
        "access_tokens",
        "private_note_content_copied_elsewhere",
        "transient_error_logs",
        "model_chain_of_thought",
        "unverified_claims",
        "duplicate_facts",
    ),
)
def test_ineligible_categories_are_denied(category: str) -> None:
    with pytest.raises(MemoryWriteDenied):
        POLICY.validate(_candidate(memory_category=category))


@pytest.mark.parametrize(
    "payload",
    ("Authorization: Bearer token-value", "sk_live_TEST_STRIPE_KEY_REDACTED", "api_key=abc"),
)
def test_secret_shaped_payload_is_denied(payload: str) -> None:
    with pytest.raises(MemoryWriteDenied):
        POLICY.validate(_candidate(payload=payload))


@pytest.mark.parametrize(
    "path", ("Outside/a.md", "Friday/../Inbox/a.md", "Friday/Inbox/a.txt", "/Friday/Inbox/a.md")
)
def test_unmanaged_or_unsafe_target_is_denied(path: str) -> None:
    with pytest.raises((MemoryWriteDenied, ValueError)):
        POLICY.validate(_candidate(path=path))


def test_append_requires_hash_and_no_frontmatter() -> None:
    with pytest.raises(ValueError):
        _candidate(
            operation=MemoryWriteOperation.APPEND_MANAGED_NOTE,
            observed_content_hash=None,
            frontmatter=(),
        )
    candidate = _candidate(
        operation=MemoryWriteOperation.APPEND_MANAGED_NOTE,
        observed_content_hash="a" * 64,
        frontmatter=(),
    )
    assert POLICY.validate(candidate).approval_required


def test_create_with_hash_is_rejected_by_model() -> None:
    with pytest.raises(ValueError):
        _candidate(observed_content_hash="a" * 64)


def test_canonical_input_is_stable_and_binds_every_field() -> None:
    candidate = _candidate()
    baseline = POLICY.canonical_fingerprint_input(candidate, run_id=RUN_ID, step_id=STEP_ID)
    assert baseline == POLICY.canonical_fingerprint_input(candidate, run_id=RUN_ID, step_id=STEP_ID)
    cases = (
        ("run", candidate, RunId.new(), STEP_ID),
        ("step", candidate, RUN_ID, None),
        (
            "operation",
            _candidate(
                operation=MemoryWriteOperation.APPEND_MANAGED_NOTE,
                observed_content_hash="a" * 64,
                frontmatter=(),
            ),
            RUN_ID,
            STEP_ID,
        ),
        ("target", _candidate(path="Friday/Decisions/example.md"), RUN_ID, STEP_ID),
        (
            "hash",
            _candidate(
                operation=MemoryWriteOperation.APPEND_MANAGED_NOTE,
                observed_content_hash="b" * 64,
                frontmatter=(),
            ),
            RUN_ID,
            STEP_ID,
        ),
        ("content", _candidate(payload="Changed."), RUN_ID, STEP_ID),
        (
            "metadata",
            _candidate(frontmatter=FRONTMATTER[:-1] + (("updated_at", "2026-01-02T00:00:00Z"),)),
            RUN_ID,
            STEP_ID,
        ),
    )
    for _name, changed, run_id, step_id in cases:
        assert (
            POLICY.canonical_fingerprint_input(changed, run_id=run_id, step_id=step_id) != baseline
        )


def test_created_frontmatter_is_bounded_and_secret_free() -> None:
    validated = POLICY.validate(_candidate())
    assert dict(validated.candidate.frontmatter).keys() == {
        "friday_managed",
        "friday_memory_id",
        "source_run_id",
        "created_at",
        "updated_at",
    }
    with pytest.raises(MemoryWriteDenied):
        POLICY.validate(_candidate(frontmatter=FRONTMATTER + (("api_key", "secret"),)))


def test_policy_never_returns_an_authorized_write() -> None:
    result = POLICY.validate(_candidate())
    assert result.approval_required is True
    assert not hasattr(result, "authorized")


@pytest.mark.parametrize("managed_root", ("", "/Friday", "../Elsewhere"))
def test_policy_rejects_invalid_managed_roots(managed_root: str) -> None:
    with pytest.raises(ValueError):
        MemoryWritePolicy(managed_root=managed_root)


def test_operation_guards_cover_invalid_model_bypass() -> None:
    append_without_hash = SimpleNamespace(
        operation=MemoryWriteOperation.APPEND_MANAGED_NOTE,
        observed_content_hash=None,
    )
    create_with_hash = SimpleNamespace(
        operation=MemoryWriteOperation.CREATE_NOTE,
        observed_content_hash="a" * 64,
    )
    with pytest.raises(MemoryWriteDenied):
        POLICY._validate_operation(append_without_hash)  # type: ignore[arg-type]
    with pytest.raises(MemoryWriteDenied):
        POLICY._validate_operation(create_with_hash)  # type: ignore[arg-type]


def test_frontmatter_guards_cover_invalid_model_bypass() -> None:
    duplicate = SimpleNamespace(
        operation=MemoryWriteOperation.CREATE_NOTE,
        frontmatter=FRONTMATTER + (("updated_at", "later"),),
    )
    not_managed = SimpleNamespace(
        operation=MemoryWriteOperation.CREATE_NOTE,
        frontmatter=(("friday_managed", "false"),) + FRONTMATTER[1:],
    )
    append_with_metadata = SimpleNamespace(
        operation=MemoryWriteOperation.APPEND_MANAGED_NOTE,
        frontmatter=(("source", "bad"),),
    )
    for invalid in (duplicate, not_managed, append_with_metadata):
        with pytest.raises(MemoryWriteDenied):
            POLICY._validate_frontmatter(invalid)  # type: ignore[arg-type]


def test_high_entropy_secret_detection_handles_token_and_non_token() -> None:
    assert _has_high_entropy_token("token: A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0")
    assert not _has_high_entropy_token("ordinary prose")
