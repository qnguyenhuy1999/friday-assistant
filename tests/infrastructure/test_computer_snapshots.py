"""Snapshot fencing: every way a mutation can fail to name a live capture.

These tests all assert the same two things in different ways: the call fails,
and `driver.mutating_calls == ()`. The second is the one that matters. A gateway
that rejects *after* clicking has rejected nothing, and a test that only checked
the returned status would pass either way.

Both directions of the fence are covered — a snapshot cannot be used from
another run, and a window or element cannot be borrowed from another snapshot —
because each is a plausible bug on its own. Element ids especially: `14` means
something in every capture, and treating it as a desktop-wide handle is the
single easiest mistake to make here.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from friday.domain.identifiers import RunId
from friday.infrastructure.computer.errors import SnapshotNotFound
from friday.infrastructure.computer.models import (
    CapturedElement,
    ScreenBounds,
    ScreenSnapshot,
    SnapshotId,
    WindowInfo,
)
from friday.infrastructure.computer.snapshots import (
    SnapshotRegistry,
    SnapshotRegistrySettings,
)
from tests.infrastructure.computer_fakes import FakeComputerDriver, default_window
from tests.infrastructure.computer_harness import (
    T0,
    Harness,
    build_harness,
    failure_code,
)

MUTATION = "computer.pointer_move"


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return build_harness(tmp_path)


def _snapshot(window_id: str = "win-mail", *, at: object = None) -> ScreenSnapshot:
    return ScreenSnapshot(
        snapshot_id=SnapshotId.new(),
        captured_at=at or T0,  # type: ignore[arg-type]
        window=WindowInfo(
            window_id=window_id, title="", bounds=ScreenBounds(x=0, y=0, width=100, height=100)
        ),
    )


# --- staleness ------------------------------------------------------------


def test_expired_snapshot_rejects_mutation_before_driver_call(harness: Harness) -> None:
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()
    harness.clock.advance(11)

    result = harness.run(MUTATION, {**harness.fence(snapshot_id), "element": 14})

    assert failure_code(result) == "computer_snapshot_expired"
    assert harness.driver.mutating_calls == ()


def test_a_snapshot_is_usable_right_up_to_its_ttl(harness: Harness) -> None:
    """Guards the guard: if the TTL were mis-signed, every mutation would fail
    and the expiry test above would still pass."""
    snapshot_id = harness.capture_snapshot()
    harness.clock.advance(9)

    assert harness.run(MUTATION, {**harness.fence(snapshot_id), "element": 14}).status == (
        "succeeded"
    )


def test_unknown_snapshot_rejects_mutation_before_driver_call(harness: Harness) -> None:
    result = harness.run(MUTATION, {**harness.fence(f"cs_{'a' * 32}"), "element": 14})

    assert failure_code(result) == "computer_snapshot_not_found"
    assert harness.driver.mutating_calls == ()


@pytest.mark.parametrize("snapshot_id", ["", "not-a-snapshot", "cs_", "cs_zzz", "cs_" + "a" * 31])
def test_malformed_snapshot_ids_fail_closed(harness: Harness, snapshot_id: str) -> None:
    result = harness.run(MUTATION, {**harness.fence(snapshot_id), "element": 14})

    assert result.status == "failed"
    assert harness.driver.mutating_calls == ()


def test_snapshot_from_future_fails_closed(harness: Harness) -> None:
    """Clock skew must not grant an unbounded lifetime. A capture that appears
    to be from the future is not fresh — it is unexplained."""
    snapshot_id = harness.capture_snapshot()
    harness.clock.set(T0 - timedelta(seconds=60))

    result = harness.run(MUTATION, {**harness.fence(snapshot_id), "element": 14})

    assert failure_code(result) == "computer_snapshot_expired"
    assert harness.driver.mutating_calls == ()


# --- identity mismatch ----------------------------------------------------


def test_window_from_other_snapshot_is_rejected(tmp_path: Path) -> None:
    driver = FakeComputerDriver(
        windows=(default_window(), default_window("win-notes", is_active=False))
    )
    harness = build_harness(tmp_path, driver=driver)
    mail = harness.capture_snapshot()
    harness.driver.calls.clear()

    result = harness.run(MUTATION, {**harness.fence(mail, "win-notes"), "element": 14})

    assert failure_code(result) == "computer_snapshot_mismatch"
    assert harness.driver.mutating_calls == ()


def test_element_from_other_snapshot_is_rejected(harness: Harness) -> None:
    """An element id is snapshot-scoped. After a fresh capture with a different
    element set, the old numbering must not resolve against the new fence."""
    stale = harness.capture_snapshot()
    harness.driver.elements = (
        CapturedElement(
            element_id=99, role="button", bounds=ScreenBounds(x=1, y=1, width=5, height=5)
        ),
    )
    fresh = harness.capture_snapshot()
    harness.driver.calls.clear()

    assert failure_code(harness.run(MUTATION, {**harness.fence(fresh), "element": 14})) == (
        "computer_snapshot_mismatch"
    )
    assert harness.run(MUTATION, {**harness.fence(stale), "element": 14}).status == "succeeded"
    assert harness.driver.only_call("move_pointer")


def test_unknown_element_is_rejected(harness: Harness) -> None:
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()

    result = harness.run(MUTATION, {**harness.fence(snapshot_id), "element": 4242})

    assert failure_code(result) == "computer_snapshot_mismatch"
    assert harness.driver.mutating_calls == ()


def test_a_snapshot_cannot_fence_another_runs_mutation(harness: Harness) -> None:
    """Run scoping: even inside the TTL, one Run's observation is not another
    Run's licence to act."""
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()

    result = harness.run(
        MUTATION, {**harness.fence(snapshot_id), "element": 14}, run_id=RunId.new()
    )

    assert failure_code(result) == "computer_snapshot_mismatch"
    assert harness.driver.mutating_calls == ()


# --- coordinate bounds ----------------------------------------------------


@pytest.mark.parametrize(
    "point",
    [
        {"x": -1, "y": 10},
        {"x": 10, "y": -1},
        {"x": 1000, "y": 10},  # right edge is half-open: x < 1000
        {"x": 10, "y": 800},
        {"x": 999_999, "y": 10},
    ],
)
def test_coordinate_outside_snapshot_window_is_rejected(
    harness: Harness, point: dict[str, int]
) -> None:
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()

    result = harness.run(MUTATION, {**harness.fence(snapshot_id), **point})

    assert failure_code(result) == "computer_target_out_of_bounds"
    assert harness.driver.mutating_calls == ()


def test_a_coordinate_inside_the_captured_window_is_accepted(harness: Harness) -> None:
    snapshot_id = harness.capture_snapshot()

    result = harness.run(MUTATION, {**harness.fence(snapshot_id), "x": 999, "y": 799})

    assert result.status == "succeeded"


# --- target shape ---------------------------------------------------------


def test_target_cannot_supply_element_and_coordinates_together(harness: Harness) -> None:
    """There is no safe way to guess which one the author meant."""
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()

    result = harness.run(MUTATION, {**harness.fence(snapshot_id), "element": 14, "x": 10, "y": 10})

    assert failure_code(result) == "computer_target_invalid"
    assert harness.driver.mutating_calls == ()


@pytest.mark.parametrize("target", [{}, {"x": 10}, {"y": 10}])
def test_target_requires_element_or_coordinate_pair(
    harness: Harness, target: dict[str, int]
) -> None:
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()

    result = harness.run(MUTATION, {**harness.fence(snapshot_id), **target})

    assert failure_code(result) == "computer_target_invalid"
    assert harness.driver.mutating_calls == ()


def test_every_mutating_tool_requires_the_fence(harness: Harness) -> None:
    """No mutating tool gets an exemption, including the ones with no target."""
    payloads: dict[str, dict[str, object]] = {
        "computer.pointer_move": {"element": 14},
        "computer.click": {"element": 14},
        "computer.scroll": {"element": 14, "dy": -100},
        "computer.type_text": {"text": "hello"},
        "computer.press_key": {"key": "enter"},
        "computer.hotkey": {"key": "c", "modifiers": ["meta"]},
        "computer.focus_window": {},
    }
    for tool, payload in payloads.items():
        result = harness.run(tool, payload)  # type: ignore[arg-type]

        assert result.status == "failed", tool
        assert harness.driver.mutating_calls == (), tool


# --- registry bounds ------------------------------------------------------


def test_snapshot_registry_is_bounded() -> None:
    """Unbounded custody is a memory leak Claude can reach by looping capture."""
    registry = SnapshotRegistry(
        SnapshotRegistrySettings(ttl_seconds=60, max_snapshots=3, max_snapshots_per_run=3)
    )
    recorded = [_snapshot() for _ in range(10)]
    for snapshot in recorded:
        registry.record(snapshot, run_scope="run-a", now=T0)

    assert len(registry) == 3
    # the newest survive; the oldest are gone
    assert registry.resolve(recorded[-1].snapshot_id.value, run_scope="run-a", now=T0)
    with pytest.raises(SnapshotNotFound):
        registry.resolve(recorded[0].snapshot_id.value, run_scope="run-a", now=T0)


def test_one_run_cannot_evict_another_runs_fences() -> None:
    """Without a per-run bound, a busy run would silently invalidate every other
    run's live capture and turn their next mutation into a not-found."""
    registry = SnapshotRegistry(
        SnapshotRegistrySettings(ttl_seconds=60, max_snapshots=8, max_snapshots_per_run=2)
    )
    protected = _snapshot()
    registry.record(protected, run_scope="run-b", now=T0)
    for _ in range(6):
        registry.record(_snapshot(), run_scope="run-a", now=T0)

    assert registry.resolve(protected.snapshot_id.value, run_scope="run-b", now=T0)


def test_expired_snapshots_are_evicted() -> None:
    registry = SnapshotRegistry(
        SnapshotRegistrySettings(ttl_seconds=10, max_snapshots=8, max_snapshots_per_run=8)
    )
    registry.record(_snapshot(), run_scope="run-a", now=T0)
    assert len(registry) == 1

    later = T0 + timedelta(seconds=60)
    registry.record(_snapshot(at=later), run_scope="run-a", now=later)

    assert len(registry) == 1


def test_registry_settings_reject_an_incoherent_configuration() -> None:
    with pytest.raises(ValueError, match="ttl_seconds must be positive"):
        SnapshotRegistrySettings(ttl_seconds=0)
    with pytest.raises(ValueError, match="max_snapshots must be positive"):
        SnapshotRegistrySettings(max_snapshots=0)
    with pytest.raises(ValueError, match="must not exceed max_snapshots"):
        SnapshotRegistrySettings(max_snapshots=2, max_snapshots_per_run=3)


def test_recording_requires_a_run_scope() -> None:
    """An unscoped snapshot would be usable by any run, which is the fence it
    exists to provide."""
    registry = SnapshotRegistry()

    with pytest.raises(ValueError, match="run_scope must not be empty"):
        registry.record(_snapshot(), run_scope="", now=T0)


def test_the_registry_exposes_its_ttl_for_reporting() -> None:
    """capture output tells Claude how long its fence lasts, so the value has to
    come from the same place the fence is evaluated."""
    registry = SnapshotRegistry(SnapshotRegistrySettings(ttl_seconds=7.5))

    assert registry.ttl_seconds == 7.5


def test_the_total_bound_evicts_across_runs() -> None:
    """The per-run bound protects other runs; the total bound protects the
    process. Both have to hold at once."""
    registry = SnapshotRegistry(
        SnapshotRegistrySettings(ttl_seconds=60, max_snapshots=3, max_snapshots_per_run=2)
    )
    for index in range(4):
        registry.record(_snapshot(), run_scope=f"run-{index}", now=T0)

    assert len(registry) == 3
