"""Shared fixtures for contract (JSON Schema) tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = REPO_ROOT / "packages" / "contracts" / "schemas" / "v1"


def schema_files() -> list[Path]:
    return sorted(SCHEMA_ROOT.rglob("*.json"))


def load_schema(path: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))


def build_registry() -> Registry:
    resources = [
        (schema["$id"], Resource.from_contents(schema, default_specification=DRAFT202012))
        for schema in (load_schema(path) for path in schema_files())
    ]
    return Registry().with_resources(resources)


@pytest.fixture
def registry() -> Registry:
    return build_registry()


@pytest.fixture(params=schema_files(), ids=lambda p: str(p.relative_to(SCHEMA_ROOT)))
def schema_path(request: pytest.FixtureRequest) -> Path:
    path: Path = request.param
    return path
