"""Small, filesystem-only locks used while promoting derived indexes."""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class FileLock:
    """An atomic mkdir lock with bounded, conservative stale-lock recovery."""

    path: Path
    ttl_seconds: float
    owner: str
    _token: str = field(default="", init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if not self.owner:
            raise ValueError("owner must not be empty")

    def acquire(self) -> bool:
        for _ in range(2):
            try:
                self.path.mkdir()
            except FileExistsError:
                if not self._is_stale() or not self._remove_stale():
                    return False
                continue
            self._write_owner()
            return True
        return False

    def release(self) -> None:
        owner = self._read_owner()
        if owner is None or owner.get("token") != self._token:
            return
        shutil.rmtree(self.path, ignore_errors=True)

    def __enter__(self) -> FileLock:
        if not self.acquire():
            raise RuntimeError("index build is already in progress")
        return self

    def __exit__(self, *_: object) -> None:
        self.release()

    def _write_owner(self) -> None:
        token = uuid.uuid4().hex
        object.__setattr__(self, "_token", token)
        payload = {"owner": self.owner, "token": token, "created_at": time.time()}
        (self.path / "owner.json").write_text(json.dumps(payload), encoding="utf-8")

    def _read_owner(self) -> dict[str, object] | None:
        try:
            value = json.loads((self.path / "owner.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _is_stale(self) -> bool:
        owner = self._read_owner()
        if owner is None:
            return False
        created_at = owner.get("created_at")
        return isinstance(created_at, (int, float)) and time.time() - created_at > self.ttl_seconds

    def _remove_stale(self) -> bool:
        """Rename first: never remove a lock that changed after we inspected it."""
        quarantine = self.path.with_name(f"{self.path.name}.stale-{uuid.uuid4().hex}")
        try:
            os.replace(self.path, quarantine)
        except OSError:
            return False
        shutil.rmtree(quarantine, ignore_errors=True)
        return True
