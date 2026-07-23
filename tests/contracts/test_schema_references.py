"""Every $ref in the v1 schema set resolves offline against the local
schema registry -- no schema depends on a live network fetch."""

from __future__ import annotations

import json

import jsonschema
import pytest
from referencing.exceptions import Unresolvable

from tests.contracts.conftest import build_registry, load_schema, schema_files


def _all_refs(node: object) -> list[str]:
    refs: list[str] = []
    if isinstance(node, dict):
        if "$ref" in node and isinstance(node["$ref"], str):
            refs.append(node["$ref"])
        for value in node.values():
            refs.extend(_all_refs(value))
    elif isinstance(node, list):
        for item in node:
            refs.extend(_all_refs(item))
    return refs


def test_every_ref_resolves_offline() -> None:
    registry = build_registry()
    unresolved: list[str] = []
    for path in schema_files():
        schema = load_schema(path)
        resolver = registry.resolver(base_uri=schema["$id"])
        for ref in _all_refs(schema):
            try:
                resolver.lookup(ref)
            except Unresolvable:
                unresolved.append(f"{path}: unresolved $ref {ref!r}")
    assert unresolved == []


def test_registry_never_performs_network_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative fixture: any network call while validating raises, proving
    resolution here never depends on fetching a remote $id."""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access attempted during offline $ref resolution")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    registry = build_registry()
    task = load_schema(next(p for p in schema_files() if p.name == "task.json"))
    validator = jsonschema.Draft202012Validator(task, registry=registry)
    validator.validate(
        json.loads(
            '{"id": "11111111-1111-1111-1111-111111111111", "title": "t", '
            '"description": "", "status": "pending", '
            '"created_at": "2026-01-01T00:00:00Z", "started_at": null, '
            '"completed_at": null, "failed_at": null, "cancelled_at": null, '
            '"failure": null}'
        )
    )
