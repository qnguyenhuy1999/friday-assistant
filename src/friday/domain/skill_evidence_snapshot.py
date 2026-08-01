"""Bounded immutable evidence selections used by improvement proposals."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import SkillEvidenceSnapshotId, SkillId, SkillRevisionId
from friday.domain.json_value import JsonValue, ensure_json_value
from friday.domain.time import ensure_utc

MAX_EVIDENCE_ENTRIES = 200
MAX_EVIDENCE_ID_LENGTH = 128
EVIDENCE_KINDS = frozenset({"usage", "feedback", "manual"})


def canonicalize_evidence(evidence: JsonValue) -> JsonValue:
    """Normalize one bounded, provenance-bearing evidence package.

    The public/manual seed shape from the first Phase 20 step (``items``) is
    accepted only as input and is converted to the canonical versioned
    ``entries`` shape.  Persisted snapshots always use that canonical shape.
    """
    value = ensure_json_value(evidence, path="SkillEvidenceSnapshot.evidence")
    entries: list[JsonValue]
    if isinstance(value, dict) and set(value) == {"version", "entries"}:
        if value.get("version") != 1:
            raise DomainValidationError("evidence snapshot version is unsupported")
        raw_entries = value.get("entries")
        if not isinstance(raw_entries, list):
            raise DomainValidationError("evidence snapshot entries are invalid or unbounded")
        entries = raw_entries
    elif isinstance(value, dict) and set(value) == {"items"}:
        items = value["items"]
        if not isinstance(items, list):
            raise DomainValidationError("evidence snapshot provenance is malformed")
        entries = []
        for item in items:
            if not isinstance(item, dict):
                raise DomainValidationError("evidence snapshot provenance is malformed")
            entries.append({"id": item.get("id"), "kind": "manual", "payload": item})
    elif isinstance(value, dict) and set(value) <= {"usage", "feedback"}:
        flattened: list[JsonValue] = []
        for kind in ("usage", "feedback"):
            items = value.get(kind, [])
            if not isinstance(items, list):
                raise DomainValidationError("evidence snapshot provenance is malformed")
            for item in items:
                if not isinstance(item, dict):
                    raise DomainValidationError("evidence snapshot provenance is malformed")
                flattened.append({"id": item.get("id"), "kind": kind, "payload": item})
        entries = flattened
    else:
        raise DomainValidationError("evidence snapshot does not match the canonical schema")

    if not entries or len(entries) > MAX_EVIDENCE_ENTRIES:
        # An empty snapshot is valid for a policy that has no observations;
        # the canonical representation still carries an explicit empty set.
        if entries != []:
            raise DomainValidationError("evidence snapshot entries are invalid or unbounded")
        entries = []
    normalized: list[JsonValue] = []
    ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"id", "kind", "payload"}:
            raise DomainValidationError("evidence snapshot entry provenance is malformed")
        entry_id = entry.get("id")
        evidence_kind = entry.get("kind")
        if (
            not isinstance(entry_id, str)
            or not entry_id
            or len(entry_id) > MAX_EVIDENCE_ID_LENGTH
            or entry_id in ids
            or not isinstance(evidence_kind, str)
            or evidence_kind not in EVIDENCE_KINDS
            or not isinstance(entry.get("payload"), (dict, list, str, int, float, bool, type(None)))
        ):
            raise DomainValidationError("evidence snapshot entry provenance is malformed")
        if evidence_kind in {"usage", "feedback"} and not isinstance(entry.get("payload"), dict):
            raise DomainValidationError("evidence snapshot entry provenance is malformed")
        ids.add(entry_id)
        normalized.append(
            {
                "id": entry_id,
                "kind": evidence_kind,
                "payload": ensure_json_value(entry["payload"], path="evidence.entry.payload"),
            }
        )
    return {"version": 1, "entries": normalized}


def evidence_ids_from_payload(evidence: JsonValue) -> frozenset[str]:
    canonical = canonicalize_evidence(evidence)
    canonical_object = cast(dict[str, JsonValue], canonical)
    entries = canonical_object["entries"]
    assert isinstance(entries, list)
    return frozenset(str(entry["id"]) for entry in entries if isinstance(entry, dict))


def evidence_payload_hash(evidence: JsonValue) -> str:
    canonical = canonicalize_evidence(evidence)
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class SkillEvidenceSnapshot:
    id: SkillEvidenceSnapshotId
    skill_id: SkillId
    base_revision_id: SkillRevisionId
    evidence: JsonValue
    content_sha256: str
    created_at: datetime

    @classmethod
    def new(
        cls,
        *,
        id: SkillEvidenceSnapshotId,
        skill_id: SkillId,
        base_revision_id: SkillRevisionId,
        evidence: JsonValue,
        created_at: datetime,
    ) -> SkillEvidenceSnapshot:
        normalized = canonicalize_evidence(evidence)
        payload = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(payload) > 64_000:
            raise DomainValidationError("evidence snapshot exceeds the bounded package size")
        return cls(
            id,
            skill_id,
            base_revision_id,
            normalized,
            hashlib.sha256(payload).hexdigest(),
            ensure_utc(created_at),
        )

    def __post_init__(self) -> None:
        if len(self.content_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.content_sha256
        ):
            raise DomainValidationError("evidence snapshot hash must be lowercase sha256")
        canonical = canonicalize_evidence(self.evidence)
        if evidence_payload_hash(canonical) != self.content_sha256:
            raise DomainValidationError("evidence snapshot hash mismatch")
        canonical_object = cast(dict[str, JsonValue], canonical)
        entries = canonical_object["entries"]
        assert isinstance(entries, list)
        for entry in entries:
            if not isinstance(entry, dict) or entry["kind"] not in {"usage", "feedback"}:
                continue
            payload = entry["payload"]
            if not isinstance(payload, dict):
                raise DomainValidationError("evidence snapshot provenance is malformed")
            if payload.get("skill_id") != str(self.skill_id) or payload.get("revision_id") != str(
                self.base_revision_id
            ):
                raise DomainValidationError("evidence snapshot crosses Skill revision provenance")
        if (
            len(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8"))
            > 64_000
        ):
            raise DomainValidationError("evidence snapshot exceeds the bounded package size")
        object.__setattr__(self, "evidence", canonical)
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
