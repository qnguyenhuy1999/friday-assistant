"""Serialization for non-sensitive, derived Graphify index metadata."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

_SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class IndexMetadata:
    schema_version: int
    graphify_version: str
    vault_identity_hash: str
    source_snapshot_hash: str
    included_file_count: int
    source_total_bytes: int
    built_at: datetime
    build_duration_seconds: float
    graph_checksum: str
    node_count: int
    edge_count: int
    # Kept last with a default so active indexes produced before durable
    # snapshot provenance can be read while the next rebuild upgrades them.
    snapshot_id: str = ""

    def __post_init__(self) -> None:
        if self.schema_version not in {1, _SCHEMA_VERSION}:
            raise ValueError("unsupported index metadata schema version")
        if not all(
            (
                self.graphify_version,
                self.vault_identity_hash,
                self.source_snapshot_hash,
                self.graph_checksum,
            )
        ):
            raise ValueError("index metadata identity fields must not be empty")
        if (
            min(self.included_file_count, self.source_total_bytes, self.node_count, self.edge_count)
            < 0
        ):
            raise ValueError("index metadata counts must not be negative")
        if self.build_duration_seconds < 0:
            raise ValueError("build_duration_seconds must not be negative")

    def to_json(self) -> str:
        value = asdict(self)
        value["built_at"] = self.built_at.isoformat()
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> IndexMetadata:
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("invalid index metadata fields")
            fields = set(cls.__dataclass_fields__)
            legacy_fields = fields - {"snapshot_id"}
            if set(value) == legacy_fields:
                # A pre-v2 index has no row in memory_index_snapshots to
                # satisfy the retrieval-record FK.  Preserve its search
                # usability, but do not invent a durable provenance ID.
                value["snapshot_id"] = ""
            elif set(value) != fields:
                raise ValueError("invalid index metadata fields")
            value["built_at"] = datetime.fromisoformat(value["built_at"])
            return cls(**value)
        except (TypeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("invalid index metadata") from exc

    def write(self, directory: Path) -> None:
        path = directory / "index-metadata.json"
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def read(cls, directory: Path) -> IndexMetadata:
        try:
            return cls.from_json((directory / "index-metadata.json").read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError("index metadata is unavailable") from exc
