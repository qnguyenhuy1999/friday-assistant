from __future__ import annotations

# mypy: disable-error-code=no-untyped-def
from datetime import UTC, datetime

import pytest

from friday.infrastructure.memory.index_metadata import IndexMetadata


def _metadata() -> IndexMetadata:
    return IndexMetadata(
        1,
        "0.9.22",
        "vault-hash",
        "snapshot-hash",
        2,
        42,
        datetime(2026, 1, 2, tzinfo=UTC),
        0.5,
        "graph-hash",
        3,
        4,
    )


def test_metadata_round_trip_preserves_all_values(tmp_path):
    metadata = _metadata()

    metadata.write(tmp_path)

    assert IndexMetadata.read(tmp_path) == metadata


def test_metadata_rejects_a_corrupt_or_truncated_file(tmp_path):
    (tmp_path / "index-metadata.json").write_text('{"schema_version":', encoding="utf-8")

    with pytest.raises(ValueError, match="invalid index metadata"):
        IndexMetadata.read(tmp_path)


def test_metadata_contains_no_note_content_or_absolute_vault_path(tmp_path):
    metadata = _metadata()
    metadata.write(tmp_path)

    raw = (tmp_path / "index-metadata.json").read_text(encoding="utf-8")

    assert "private note body" not in raw
    assert str(tmp_path) not in raw


def test_metadata_rejects_missing_file_and_invalid_values(tmp_path):
    with pytest.raises(ValueError, match="unavailable"):
        IndexMetadata.read(tmp_path)
    with pytest.raises(ValueError, match="identity"):
        IndexMetadata(1, "", "vault", "snapshot", 0, 0, datetime.now(UTC), 0, "graph", 0, 0)
    with pytest.raises(ValueError, match="schema"):
        IndexMetadata(3, "version", "vault", "snapshot", 0, 0, datetime.now(UTC), 0, "graph", 0, 0)
    with pytest.raises(ValueError, match="counts"):
        IndexMetadata(1, "version", "vault", "snapshot", -1, 0, datetime.now(UTC), 0, "graph", 0, 0)
    with pytest.raises(ValueError, match="duration"):
        IndexMetadata(1, "version", "vault", "snapshot", 0, 0, datetime.now(UTC), -1, "graph", 0, 0)


def test_metadata_rejects_extra_or_non_object_json():
    with pytest.raises(ValueError, match="invalid index metadata"):
        IndexMetadata.from_json("[]")
    with pytest.raises(ValueError, match="invalid index metadata"):
        IndexMetadata.from_json('{"extra":true}')
