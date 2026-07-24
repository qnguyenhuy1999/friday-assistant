"""API delivery settings. Delivery-owned configuration only — no secrets are
required for local operation, and defaults must stay safe for local
development (loopback binding, a workspace-local SQLite file).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_DEFAULT_DATABASE_URL = "sqlite:///./friday.db"
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8000
_DEFAULT_SSE_POLL_INTERVAL_SECONDS = 0.5


@dataclass(frozen=True, slots=True)
class ApiSettings:
    database_url: str
    host: str
    port: int
    sse_poll_interval_seconds: float

    @classmethod
    def from_env(cls) -> ApiSettings:
        return cls(
            database_url=os.environ.get("FRIDAY_API_DATABASE_URL", _DEFAULT_DATABASE_URL),
            host=os.environ.get("FRIDAY_API_HOST", _DEFAULT_HOST),
            port=int(os.environ.get("FRIDAY_API_PORT", _DEFAULT_PORT)),
            sse_poll_interval_seconds=float(
                os.environ.get(
                    "FRIDAY_API_SSE_POLL_INTERVAL_SECONDS",
                    _DEFAULT_SSE_POLL_INTERVAL_SECONDS,
                )
            ),
        )
