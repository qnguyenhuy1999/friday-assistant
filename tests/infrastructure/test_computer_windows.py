"""`computer.focus_window`, plus the validation edges of the supporting pieces.

Focusing looks harmless next to clicking, and is not: it changes which
application receives every subsequent keystroke. A `type_text` fenced against
one window, executed just after focus moved elsewhere, is how a message intended
for a notes app ends up somewhere else. So it is mutating, approval-gated, and
snapshot-fenced like everything else.

The window id handed to the driver is the one **the snapshot recorded**, not the
one the tool input supplied. Those are equal by the time the fence passes, but
taking it from the snapshot means a future change to the fence cannot
accidentally let caller-supplied text through.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from friday.application.tool_gateway import ToolCall
from friday.infrastructure.computer.artifacts import (
    ScreenshotStore,
    ScreenshotStoreSettings,
    ScreenshotTooLarge,
)
from friday.infrastructure.computer.context import ComputerToolContext
from friday.infrastructure.computer.models import ScreenBounds, Screenshot, SnapshotId
from tests.infrastructure.computer_fakes import (
    PNG_BYTES,
    FakeComputerDriver,
    default_window,
)
from tests.infrastructure.computer_harness import (
    T0,
    Harness,
    build_harness,
    failure_code,
    output_of,
)


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return build_harness(tmp_path)


# --- focus_window ---------------------------------------------------------


def test_focus_window_requires_approval(harness: Harness) -> None:
    assessment = harness.gateway.assess(ToolCall(tool="computer.focus_window", tool_input={}))

    assert assessment.read_only is False
    assert assessment.approval_required is True


def test_focus_window_raises_the_captured_window(harness: Harness) -> None:
    snapshot_id = harness.capture_snapshot()

    output = output_of(harness.run("computer.focus_window", harness.fence(snapshot_id)))

    assert output == {"window_id": "win-mail"}
    assert harness.driver.only_call("focus_window").argument("window_id") == "win-mail"


def test_focus_window_actually_changes_the_active_window(tmp_path: Path) -> None:
    """End to end against the fake desktop: the window really becomes active."""
    driver = FakeComputerDriver(
        windows=(
            default_window("win-mail", is_active=True),
            default_window("win-notes", is_active=False),
        )
    )
    harness = build_harness(tmp_path, driver=driver)
    notes = harness.capture({"window_id": "win-notes", "include_screenshot": False})
    snapshot_id = notes["snapshot_id"]
    assert isinstance(snapshot_id, str)

    harness.run("computer.focus_window", harness.fence(snapshot_id, "win-notes"))

    active = output_of(harness.run("computer.active_window"))["window"]
    assert isinstance(active, dict)
    assert active["window_id"] == "win-notes"


def test_focus_window_rejects_an_unfenced_window(harness: Harness) -> None:
    """No arbitrary window id Claude composed or remembered from an expired
    capture — the fence is Friday's own record of what it observed."""
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()

    result = harness.run("computer.focus_window", harness.fence(snapshot_id, "win-elsewhere"))

    assert failure_code(result) == "computer_snapshot_mismatch"
    assert harness.driver.mutating_calls == ()


def test_focus_window_rejects_unknown_fields(harness: Harness) -> None:
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()

    result = harness.run("computer.focus_window", {**harness.fence(snapshot_id), "activate": True})

    assert failure_code(result) == "tool_invalid_input"
    assert harness.driver.mutating_calls == ()


def test_focus_window_after_expiry_is_refused(harness: Harness) -> None:
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()
    harness.clock.advance(11)

    result = harness.run("computer.focus_window", harness.fence(snapshot_id))

    assert failure_code(result) == "computer_snapshot_expired"
    assert harness.driver.mutating_calls == ()


# --- screenshot store edges ----------------------------------------------


def _store(tmp_path: Path, **overrides: object) -> ScreenshotStore:
    settings: dict[str, object] = {"workspace_root": tmp_path}
    settings.update(overrides)
    return ScreenshotStore(ScreenshotStoreSettings(**settings))  # type: ignore[arg-type]


def test_the_store_rejects_a_nonpositive_capture_ceiling(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_capture_bytes must be positive"):
        ScreenshotStoreSettings(workspace_root=tmp_path, max_capture_bytes=0)


@pytest.mark.parametrize("root", ["/etc/friday", "../escape", "", "a/../../b"])
def test_the_artifact_root_must_stay_workspace_relative(tmp_path: Path, root: str) -> None:
    """An absolute or traversing root would put images outside the workspace the
    rest of the tool surface is confined to."""
    with pytest.raises(ValueError, match="workspace-relative"):
        ScreenshotStoreSettings(workspace_root=tmp_path, artifact_root=root)


@pytest.mark.parametrize(
    "invocation_id", ["../escape", "a/b", "", ".hidden", "with space", "x" * 200]
)
def test_the_store_refuses_an_unsafe_invocation_id(tmp_path: Path, invocation_id: str) -> None:
    """The id becomes a path component. It comes from Friday, not from Claude,
    but a component that could traverse is worth refusing at the boundary."""
    with pytest.raises(ValueError, match="safe path component"):
        _store(tmp_path).persist(
            Screenshot(data=PNG_BYTES, media_type="image/png", width=10, height=10),
            invocation_id=invocation_id,
            snapshot_id=SnapshotId.new(),
        )


def test_a_failed_write_leaves_no_temporary_file_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Atomic publication means a reader never sees a half-written PNG — and a
    failure must not leave a stray temp file either."""

    def exploding_replace(source: object, target: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", exploding_replace)

    with pytest.raises(OSError, match="disk full"):
        _store(tmp_path).persist(
            Screenshot(data=PNG_BYTES, media_type="image/png", width=10, height=10),
            invocation_id="inv-1",
            snapshot_id=SnapshotId.new(),
        )

    assert list(tmp_path.rglob(".friday-capture-*")) == []


def test_the_store_refuses_bytes_over_the_configured_ceiling(tmp_path: Path) -> None:
    with pytest.raises(ScreenshotTooLarge, match="capture ceiling"):
        _store(tmp_path, max_capture_bytes=4).persist(
            Screenshot(data=PNG_BYTES, media_type="image/png", width=10, height=10),
            invocation_id="inv-1",
            snapshot_id=SnapshotId.new(),
        )


def test_a_custom_artifact_root_is_honoured(tmp_path: Path) -> None:
    candidate = _store(tmp_path, artifact_root="captures/").persist(
        Screenshot(data=PNG_BYTES, media_type="image/png", width=10, height=10),
        invocation_id="inv-1",
        snapshot_id=SnapshotId.new(),
    )

    assert candidate.location.startswith("captures/inv-1/")
    assert (tmp_path / candidate.location).is_file()


# --- execution context validation ----------------------------------------


@pytest.mark.parametrize(
    ("run_scope", "invocation_id", "now"),
    [
        ("", "inv-1", T0),
        ("   ", "inv-1", T0),
        ("run-1", "", T0),
        ("run-1", "  ", T0),
        ("run-1", "inv-1", datetime(2026, 1, 1)),  # naive
    ],
)
def test_the_tool_context_refuses_incoherent_identity(
    run_scope: str, invocation_id: str, now: datetime
) -> None:
    with pytest.raises(ValueError):
        ComputerToolContext(run_scope=run_scope, invocation_id=invocation_id, now=now)


def test_the_tool_context_normalizes_to_utc() -> None:
    """TTL comparisons are only sound if both sides are UTC-aware."""
    context = ComputerToolContext(
        run_scope="run-1",
        invocation_id="inv-1",
        now=datetime(2026, 1, 1, 12, tzinfo=UTC),
    )

    assert context.now.tzinfo is UTC


# --- element geometry ----------------------------------------------------


def test_an_element_outside_its_own_captured_window_is_refused(tmp_path: Path) -> None:
    """Driver output is untrusted even when it is geometry: an element reported
    outside the window it was captured from is refused, not reconciled."""
    from friday.infrastructure.computer.models import CapturedElement

    driver = FakeComputerDriver(
        windows=(default_window(),),
        elements=(
            CapturedElement(
                element_id=7,
                role="button",
                bounds=ScreenBounds(x=5_000, y=5_000, width=10, height=10),
            ),
        ),
    )
    harness = build_harness(tmp_path, driver=driver)
    snapshot_id = harness.capture_snapshot()
    harness.driver.calls.clear()

    result = harness.run("computer.pointer_move", {**harness.fence(snapshot_id), "element": 7})

    assert failure_code(result) == "computer_target_out_of_bounds"
    assert harness.driver.mutating_calls == ()
