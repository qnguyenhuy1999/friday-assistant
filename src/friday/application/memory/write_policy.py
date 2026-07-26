"""Pure, conservative policy for proposed Friday-managed memory writes.

Secret-shape detection is shared with desktop text entry (see
friday.application.secret_shapes): it is defence in depth, not a guarantee that
a value is safe to persist.  Callers must still obtain an exact-action approval
before any validated proposal is written.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum

from friday.application.memory.errors import MemoryWriteDenied
from friday.application.memory.models import MemoryWriteCandidate, MemoryWriteOperation
from friday.application.secret_shapes import contains_secret_shape
from friday.domain.identifiers import RunId, RunStepId

_DEFAULT_MANAGED_ROOT = "Friday"
_PERMITTED_TARGET_SUFFIXES = ("Inbox", "Preferences", "Projects", "Decisions")
_REQUIRED_FRONTMATTER_KEYS = frozenset(
    {"friday_managed", "friday_memory_id", "source_run_id", "created_at", "updated_at"}
)


class MemoryCategory(StrEnum):
    DURABLE_USER_PREFERENCE = "durable_user_preference"
    EXPLICIT_DECISION = "explicit_decision"
    PROJECT_ARCHITECTURE_DECISION = "project_architecture_decision"
    STABLE_ENVIRONMENT_FACT = "stable_environment_fact"
    REUSABLE_TROUBLESHOOTING_RESOLUTION = "reusable_troubleshooting_resolution"
    LONG_LIVED_WORKFLOW_RULE = "long_lived_workflow_rule"
    EXPLICIT_USER_REQUEST_TO_REMEMBER = "explicit_user_request_to_remember"


ELIGIBLE_MEMORY_CATEGORIES = frozenset(MemoryCategory)


@dataclass(frozen=True, slots=True)
class ValidatedMemoryWrite:
    """A policy-approved proposal; it is deliberately not an authorization."""

    candidate: MemoryWriteCandidate
    canonical_new_content: str

    @property
    def approval_required(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class MemoryWritePolicy:
    """Validate only curated writes below Friday's managed vault area."""

    managed_root: str = _DEFAULT_MANAGED_ROOT

    def __post_init__(self) -> None:
        if not self.managed_root or self.managed_root.startswith("/"):
            raise ValueError("MemoryWritePolicy.managed_root must be a relative path")
        if ".." in self.managed_root.split("/"):
            raise ValueError("MemoryWritePolicy.managed_root must not escape the vault")

    def validate(self, candidate: MemoryWriteCandidate) -> ValidatedMemoryWrite:
        """Validate a proposal before calculating its approval fingerprint."""
        self._validate_category(candidate.memory_category)
        self._validate_target(candidate.path)
        self._validate_operation(candidate)
        self._validate_frontmatter(candidate)
        self._reject_secret_content(candidate)
        return ValidatedMemoryWrite(candidate=candidate, canonical_new_content=candidate.payload)

    def canonical_fingerprint_input(
        self,
        candidate: MemoryWriteCandidate,
        *,
        run_id: RunId | str,
        step_id: RunStepId | str | None = None,
    ) -> bytes:
        """Canonical material for the exact-action authorization fingerprint."""
        validated = self.validate(candidate)
        material = {
            "observed_prior_content_hash": candidate.observed_content_hash,
            "frontmatter": list(candidate.frontmatter),
            "memory_operation": candidate.operation.value,
            "new_content": validated.canonical_new_content,
            "run_id": str(run_id),
            "step_id": str(step_id) if step_id is not None else None,
            "vault_relative_target": candidate.path,
        }
        return json.dumps(
            material, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")

    def _validate_category(self, category: str) -> None:
        if category not in ELIGIBLE_MEMORY_CATEGORIES:
            raise MemoryWriteDenied("memory category is not eligible for curated storage")

    def _validate_target(self, path: str) -> None:
        parts = path.split("/")
        if (
            not path
            or path.startswith(("/", "~"))
            or ".." in parts
            or any(part in {"", "."} for part in parts)
            or not path.endswith(".md")
            or not path.startswith(self._permitted_target_prefixes)
        ):
            raise MemoryWriteDenied("target must be a managed, vault-relative Markdown note")

    @property
    def _permitted_target_prefixes(self) -> tuple[str, ...]:
        root = self.managed_root.rstrip("/")
        return tuple(f"{root}/{suffix}/" for suffix in _PERMITTED_TARGET_SUFFIXES)

    def _validate_operation(self, candidate: MemoryWriteCandidate) -> None:
        if (
            candidate.operation is MemoryWriteOperation.APPEND_MANAGED_NOTE
            and not candidate.observed_content_hash
        ):
            raise MemoryWriteDenied("append requires an observed content hash")
        if (
            candidate.operation is MemoryWriteOperation.CREATE_NOTE
            and candidate.observed_content_hash is not None
        ):
            raise MemoryWriteDenied("create must not specify an observed content hash")

    def _validate_frontmatter(self, candidate: MemoryWriteCandidate) -> None:
        metadata = dict(candidate.frontmatter)
        if len(metadata) != len(candidate.frontmatter):
            raise MemoryWriteDenied("frontmatter keys must be unique")
        if candidate.operation is MemoryWriteOperation.CREATE_NOTE:
            if set(metadata) != _REQUIRED_FRONTMATTER_KEYS:
                raise MemoryWriteDenied("created notes require bounded Friday frontmatter")
            if metadata["friday_managed"] != "true":
                raise MemoryWriteDenied("created notes must be marked friday_managed")
        elif metadata:
            raise MemoryWriteDenied("append proposals must not alter frontmatter")

    def _reject_secret_content(self, candidate: MemoryWriteCandidate) -> None:
        material = "\n".join(
            (candidate.payload, *(f"{key}: {value}" for key, value in candidate.frontmatter))
        )
        if contains_secret_shape(material):
            raise MemoryWriteDenied("proposal contains secret-shaped content")
