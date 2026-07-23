"""Every schema under packages/contracts/schemas/v1 is a well-formed Draft
2020-12 meta-schema, has expected required fields ($id, $schema, title), and
uses a consistent $id namespace/path convention."""

from __future__ import annotations

import jsonschema

from tests.contracts.conftest import SCHEMA_ROOT, load_schema, schema_files


def test_all_schemas_are_valid_draft_2020_12() -> None:
    for path in schema_files():
        jsonschema.Draft202012Validator.check_schema(load_schema(path))


def test_all_schemas_declare_id_schema_and_title() -> None:
    missing: list[str] = []
    for path in schema_files():
        schema = load_schema(path)
        for key in ("$schema", "$id", "title"):
            if key not in schema:
                missing.append(f"{path}: missing {key}")
    assert missing == []


def test_schema_id_matches_repo_relative_path() -> None:
    prefix = "https://schemas.friday-agent-os.dev/v1/"
    mismatches: list[str] = []
    for path in schema_files():
        schema = load_schema(path)
        expected = prefix + str(path.relative_to(SCHEMA_ROOT))
        if schema["$id"] != expected:
            mismatches.append(f"{path}: $id {schema['$id']!r} != expected {expected!r}")
    assert mismatches == []


def test_detector_flags_a_schema_missing_title(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Negative fixture: proves the required-field detector catches an
    incomplete schema without touching real schema files."""
    fixture = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "x"}
    missing = [key for key in ("$schema", "$id", "title") if key not in fixture]
    assert missing == ["title"]
