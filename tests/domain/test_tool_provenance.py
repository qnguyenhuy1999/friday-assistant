from __future__ import annotations

import pytest

from friday.domain.errors import DomainValidationError
from friday.domain.tool_provenance import MAX_PROVENANCE_FIELD_CHARS, ToolProvenance


def _provenance(**overrides: str) -> ToolProvenance:
    values = {
        "kind": "mcp",
        "target": "github",
        "remote_name": "create_issue",
        "binding_fingerprint": "a" * 64,
    }
    values.update(overrides)
    return ToolProvenance(**values)


def test_valid_provenance_keeps_every_field() -> None:
    assert _provenance().target == "github"


def test_provenance_is_immutable() -> None:
    with pytest.raises(AttributeError):
        _provenance().target = "other"  # type: ignore[misc]


@pytest.mark.parametrize("field", ["kind", "target", "remote_name", "binding_fingerprint"])
def test_empty_or_blank_field_is_rejected(field: str) -> None:
    with pytest.raises(DomainValidationError, match=field):
        _provenance(**{field: "   "})


@pytest.mark.parametrize("field", ["kind", "target", "remote_name", "binding_fingerprint"])
def test_oversized_field_is_rejected(field: str) -> None:
    with pytest.raises(DomainValidationError, match=field):
        _provenance(**{field: "x" * (MAX_PROVENANCE_FIELD_CHARS + 1)})


def test_field_at_the_limit_is_accepted() -> None:
    assert len(_provenance(target="x" * MAX_PROVENANCE_FIELD_CHARS).target) == 200
