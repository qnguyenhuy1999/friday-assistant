from __future__ import annotations

# mypy: disable-error-code=no-untyped-def
import json
import time

import pytest

from friday.infrastructure.memory.file_lock import FileLock


def test_live_lock_is_not_stolen(tmp_path):
    first = FileLock(tmp_path / "lock", 60, "one")
    second = FileLock(tmp_path / "lock", 60, "two")

    assert first.acquire()
    assert not second.acquire()
    first.release()


def test_stale_lock_is_recovered(tmp_path):
    path = tmp_path / "lock"
    path.mkdir()
    (path / "owner.json").write_text(json.dumps({"created_at": time.time() - 10}), encoding="utf-8")
    lock = FileLock(path, 1, "new")

    assert lock.acquire()
    lock.release()


def test_invalid_lock_settings_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="positive"):
        FileLock(tmp_path / "lock", 0, "owner")
    with pytest.raises(ValueError, match="empty"):
        FileLock(tmp_path / "lock", 1, "")


def test_release_does_not_remove_a_lock_owned_by_someone_else(tmp_path):
    lock = FileLock(tmp_path / "lock", 1, "owner")
    assert lock.acquire()
    (lock.path / "owner.json").write_text(json.dumps({"token": "other"}), encoding="utf-8")

    lock.release()

    assert lock.path.exists()


def test_context_manager_releases_the_lock(tmp_path):
    with FileLock(tmp_path / "lock", 1, "owner"):
        assert (tmp_path / "lock").exists()

    assert not (tmp_path / "lock").exists()


def test_context_manager_reports_an_existing_live_lock(tmp_path):
    first = FileLock(tmp_path / "lock", 60, "one")
    assert first.acquire()

    with (
        pytest.raises(RuntimeError, match="already in progress"),
        FileLock(tmp_path / "lock", 60, "two"),
    ):
        pass

    first.release()


@pytest.mark.parametrize("raw", ("not json", "[]"))
def test_corrupt_lock_owner_is_not_recovered(tmp_path, raw):
    path = tmp_path / "lock"
    path.mkdir()
    (path / "owner.json").write_text(raw, encoding="utf-8")

    assert not FileLock(path, 1, "owner").acquire()


def test_stale_lock_rename_failure_keeps_the_lock(tmp_path, monkeypatch):
    path = tmp_path / "lock"
    path.mkdir()
    (path / "owner.json").write_text(json.dumps({"created_at": time.time() - 10}), encoding="utf-8")
    monkeypatch.setattr(
        "friday.infrastructure.memory.file_lock.os.replace",
        lambda *_: (_ for _ in ()).throw(OSError()),
    )

    assert not FileLock(path, 1, "owner").acquire()
