from __future__ import annotations

import pytest

from friday.application.errors import ToolInputInvalid
from friday.infrastructure.mcp.schema import normalize_input_schema, validate_input


def test_schema_normalization_removes_annotations_and_validation_fails_closed() -> None:
    schema = normalize_input_schema(
        {"type": "object", "description": "ignore", "properties": {"key": {"type": "string"}}},
        max_bytes=1024,
    )
    assert "description" not in schema
    with pytest.raises(ToolInputInvalid):
        validate_input(schema, {"unknown": 1})
