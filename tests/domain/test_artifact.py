"""Artifact: metadata-only record. Field validation, JSON-compatible
metadata, immutability."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from friday.domain.artifact import Artifact, ArtifactKind
from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import ArtifactId, RunId

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _artifact(**overrides: object) -> Artifact:
    defaults: dict[str, object] = {
        "id": ArtifactId.new(),
        "run_id": RunId.new(),
        "kind": ArtifactKind.FILE,
        "name": "output.txt",
        "media_type": "text/plain",
        "location": "/tmp/output.txt",
        "created_at": T0,
    }
    defaults.update(overrides)
    return Artifact(**defaults)  # type: ignore[arg-type]


def test_valid_artifact_constructs() -> None:
    artifact = _artifact()
    assert artifact.name == "output.txt"
    assert artifact.kind is ArtifactKind.FILE


def test_empty_name_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        _artifact(name="")


def test_empty_location_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        _artifact(location="")


def test_negative_size_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        _artifact(size=-1)


def test_zero_size_is_accepted() -> None:
    assert _artifact(size=0).size == 0


def test_non_json_metadata_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        _artifact(metadata=math.nan)


def test_artifact_is_immutable() -> None:
    artifact = _artifact()
    with pytest.raises(AttributeError):
        artifact.name = "changed"  # type: ignore[misc]


def test_all_artifact_kinds_are_constructible() -> None:
    for kind in ArtifactKind:
        _artifact(kind=kind)
