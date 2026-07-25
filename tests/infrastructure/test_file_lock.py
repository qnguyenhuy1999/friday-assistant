from __future__ import annotations

import json
import time

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
    (path / "owner.json").write_text(json.dumps({"created_at": time.time() - 10}))
    lock = FileLock(path, 1, "new")

    assert lock.acquire()
    lock.release()
