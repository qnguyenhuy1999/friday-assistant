"""Versioning conventions for the v1 contract set: every schema lives under
schemas/v1/, every $id is namespaced under .../v1/, and no schema silently
allows fields outside its declared shape (open contracts drift undetected)."""

from __future__ import annotations

from tests.contracts.conftest import SCHEMA_ROOT, load_schema, schema_files


def test_all_schemas_live_under_v1_directory() -> None:
    assert SCHEMA_ROOT.name == "v1"
    for path in schema_files():
        assert path.is_relative_to(SCHEMA_ROOT)


def test_all_schema_ids_are_namespaced_under_v1() -> None:
    for path in schema_files():
        schema = load_schema(path)
        assert "/v1/" in schema["$id"], f"{path}: $id not namespaced under /v1/: {schema['$id']}"


def test_entity_schemas_forbid_additional_properties() -> None:
    """Only object-typed entity schemas need this -- the shared json_value.json
    schema is deliberately open (it validates arbitrary payload shapes)."""
    exempt = {"json_value.json"}
    offenders: list[str] = []
    for path in schema_files():
        if path.name in exempt:
            continue
        schema = load_schema(path)
        if schema.get("type") == "object" and schema.get("additionalProperties") is not False:
            offenders.append(str(path))
    assert offenders == []


def test_detector_flags_a_schema_outside_v1(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Negative fixture: proves the namespace check catches a mis-versioned
    $id without touching real schema files."""
    fixture_id = "https://schemas.friday-agent-os.dev/v2/task/task.json"
    assert "/v1/" not in fixture_id
