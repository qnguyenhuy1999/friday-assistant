"""Pure, conservative policy for proposed Friday-managed memory writes.

The secret detector is defence in depth, not a guarantee that a value is safe
to persist.  Callers must still obtain an exact-action approval before any
validated proposal is written.
"""

from __future__ import annotations

import base64
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

from friday.application.memory.errors import MemoryWriteDenied
from friday.application.memory.models import MemoryWriteCandidate, MemoryWriteOperation
from friday.domain.identifiers import RunId, RunStepId

_DEFAULT_MANAGED_ROOT = "Friday"
_PERMITTED_TARGET_PREFIXES = (
    "Friday/Inbox/",
    "Friday/Preferences/",
    "Friday/Projects/",
    "Friday/Decisions/",
)
_REQUIRED_FRONTMATTER_KEYS = frozenset(
    {"friday_managed", "friday_memory_id", "source_run_id", "created_at", "updated_at"}
)
_SECRET_PATTERNS = (
    re.compile(r"(?im)^authorization\s*:\s*(?:bearer|basic)\s+\S+"),
    re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|secret|password|access[_-]?token)\s*[:=]\s*\S+"),
)
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9+/_=-]{32,}\b")


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
        if self.managed_root.rstrip("/") != _DEFAULT_MANAGED_ROOT:
            raise ValueError("MemoryWritePolicy.managed_root must be Friday")

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
            "expected_prior_content_hash": candidate.expected_content_hash,
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
            or not path.startswith(_PERMITTED_TARGET_PREFIXES)
        ):
            raise MemoryWriteDenied("target must be a managed, vault-relative Markdown note")

    def _validate_operation(self, candidate: MemoryWriteCandidate) -> None:
        if (
            candidate.operation is MemoryWriteOperation.APPEND_MANAGED_NOTE
            and not candidate.expected_content_hash
        ):
            raise MemoryWriteDenied("append requires an expected content hash")
        if (
            candidate.operation is MemoryWriteOperation.CREATE_NOTE
            and candidate.expected_content_hash is not None
        ):
            raise MemoryWriteDenied("create must not specify an expected content hash")

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
        if any(pattern.search(material) for pattern in _SECRET_PATTERNS) or _has_high_entropy_token(
            material
        ):
            raise MemoryWriteDenied("proposal contains secret-shaped content")


def _has_high_entropy_token(text: str) -> bool:
    """Conservatively identify token-like high-entropy strings, deterministically."""
    for token in _LONG_TOKEN.findall(text):
        try:
            decoded = base64.b64decode(token + "===", validate=False)
        except ValueError:
            decoded = token.encode("utf-8")
        if len(decoded) >= 24 and _shannon_entropy(token) >= 4.0:
            return True
    return False


def _shannon_entropy(token: str) -> float:
    length = len(token)
    frequencies = Counter(token)
    return -sum((count / length) * math.log2(count / length) for count in frequencies.values())
