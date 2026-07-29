"""Opt-in settings for operator-owned outbound message routes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from friday.infrastructure.messaging.config import MessagingRoute, load_messaging_routes


@dataclass(frozen=True, slots=True)
class MessagingSettings:
    config_path: Path | None

    @classmethod
    def from_env(cls) -> MessagingSettings:
        raw = os.environ.get("FRIDAY_MESSAGING_CONFIG_PATH", "").strip()
        return cls(Path(raw) if raw else None)

    def routes(self) -> tuple[MessagingRoute, ...]:
        return load_messaging_routes(self.config_path)
