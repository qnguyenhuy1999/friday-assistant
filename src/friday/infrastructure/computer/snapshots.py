"""The snapshot registry: bounded, transient custody of what Friday observed.

A snapshot is the fence a mutating action must bind to. It answers "was this
click proposed against a desktop that still looks like the one Claude saw?" —
and it must answer *no* by default, because the cost of a wrong yes is a click
somewhere nobody intended.

Deliberately in-process and deliberately not a domain entity. Snapshots live
for seconds; persisting them would add a migration, a repository, and a
lifecycle to state whose entire purpose is to expire. Losing the registry on
restart is correct behaviour, not data loss: a worker that just restarted has
no business acting on a pre-restart observation of a desktop it can no longer
vouch for.

Three bounds, each closing a different hole:

* **TTL** — a stale fence is no fence. Expiry is checked on lookup, not only
  on eviction, so a snapshot cannot be used just because no capture has
  happened since it aged out.
* **Total count** — an unbounded registry is a memory leak reachable by
  Claude simply calling capture in a loop.
* **Per-run count** — without it, one run could evict every other run's live
  fences and turn their next mutation into a `computer_snapshot_not_found`.

Run scoping is the fourth fence, and the one least likely to be missed by
accident: a snapshot captured by one Run can never fence a mutation proposed
by another, even inside its TTL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from friday.infrastructure.computer.errors import (
    SnapshotExpired,
    SnapshotMismatch,
    SnapshotNotFound,
)
from friday.infrastructure.computer.models import ScreenSnapshot, SnapshotId

DEFAULT_CAPTURE_TTL_SECONDS = 10.0
DEFAULT_MAX_SNAPSHOTS = 32
DEFAULT_MAX_SNAPSHOTS_PER_RUN = 8


@dataclass(frozen=True, slots=True)
class SnapshotRegistrySettings:
    ttl_seconds: float = DEFAULT_CAPTURE_TTL_SECONDS
    max_snapshots: int = DEFAULT_MAX_SNAPSHOTS
    max_snapshots_per_run: int = DEFAULT_MAX_SNAPSHOTS_PER_RUN

    def __post_init__(self) -> None:
        if self.ttl_seconds <= 0:
            raise ValueError("SnapshotRegistrySettings.ttl_seconds must be positive")
        if self.max_snapshots < 1:
            raise ValueError("SnapshotRegistrySettings.max_snapshots must be positive")
        if self.max_snapshots_per_run < 1:
            raise ValueError("SnapshotRegistrySettings.max_snapshots_per_run must be positive")
        if self.max_snapshots_per_run > self.max_snapshots:
            raise ValueError(
                "SnapshotRegistrySettings.max_snapshots_per_run must not exceed max_snapshots"
            )


@dataclass(frozen=True, slots=True)
class _Entry:
    snapshot: ScreenSnapshot
    run_scope: str


class SnapshotRegistry:
    """Bounded in-process snapshot custody. Not thread-safe by design: one
    worker executes one tool action at a time, and adding a lock would imply a
    concurrency model the execution path does not actually have."""

    def __init__(self, settings: SnapshotRegistrySettings | None = None) -> None:
        self._settings = settings or SnapshotRegistrySettings()
        # insertion-ordered: snapshot ids are unique and only ever appended,
        # so iteration order is capture order and the oldest is simply first
        self._entries: dict[str, _Entry] = {}

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def ttl_seconds(self) -> float:
        return self._settings.ttl_seconds

    def record(self, snapshot: ScreenSnapshot, *, run_scope: str, now: datetime) -> None:
        """Take custody of a fresh capture, evicting whatever must go."""
        if not run_scope:
            raise ValueError("run_scope must not be empty")
        self._evict_expired(now)
        self._enforce_per_run_bound(run_scope)
        self._entries[snapshot.snapshot_id.value] = _Entry(snapshot=snapshot, run_scope=run_scope)
        self._enforce_total_bound()

    def resolve(self, snapshot_id: str, *, run_scope: str, now: datetime) -> ScreenSnapshot:
        """Return the live snapshot a mutating action may fence against.

        Raises rather than returning None: every caller is about to touch a
        desktop, and there is no sensible "no snapshot" branch for them to take.
        A malformed id is reported as not-found rather than as invalid input —
        from Claude's side both mean "that fence is not available", and keeping
        them distinct would leak whether an id was well-formed but unknown.
        """
        try:
            identifier = SnapshotId(snapshot_id)
        except ValueError:
            raise SnapshotNotFound("snapshot_id does not name a live capture") from None
        entry = self._entries.get(identifier.value)
        if entry is None:
            raise SnapshotNotFound("snapshot_id does not name a live capture")
        if entry.snapshot.is_expired(now, ttl_seconds=self._settings.ttl_seconds):
            # drop it now so a later call cannot find it either
            del self._entries[identifier.value]
            raise SnapshotExpired("the cited capture has expired; capture again before acting")
        if entry.run_scope != run_scope:
            raise SnapshotMismatch("the cited capture belongs to a different run")
        return entry.snapshot

    def _evict_expired(self, now: datetime) -> None:
        stale = [
            key
            for key, entry in self._entries.items()
            if entry.snapshot.is_expired(now, ttl_seconds=self._settings.ttl_seconds)
        ]
        for key in stale:
            del self._entries[key]

    def _enforce_per_run_bound(self, run_scope: str) -> None:
        """Make room for one more snapshot in this run, oldest first."""
        owned = [key for key, entry in self._entries.items() if entry.run_scope == run_scope]
        excess = len(owned) - (self._settings.max_snapshots_per_run - 1)
        for key in owned[: max(0, excess)]:
            del self._entries[key]

    def _enforce_total_bound(self) -> None:
        excess = len(self._entries) - self._settings.max_snapshots
        for key in list(self._entries)[: max(0, excess)]:
            del self._entries[key]
