"""API delivery settings. Delivery-owned configuration only — no secrets are
required for local operation, and defaults must stay safe for local
development (loopback binding, a workspace-local SQLite file).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlparse

_DEFAULT_DATABASE_URL = "sqlite:///./friday.db"
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8000
_DEFAULT_SSE_POLL_INTERVAL_SECONDS = 0.5
_DEFAULT_CORS_ORIGINS: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")


def _parse_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    if value.strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if value.strip().lower() in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _parse_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _parse_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _validate_origin(origin: str) -> None:
    if "*" in origin:
        raise ValueError("FRIDAY_API_CORS_ORIGINS must not contain wildcard origins")
    parsed = urlparse(origin)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "FRIDAY_API_CORS_ORIGINS entries must be exact http(s) origins without paths"
        )


@dataclass(frozen=True, slots=True)
class ApiSettings:
    database_url: str
    host: str
    port: int
    sse_poll_interval_seconds: float
    cors_allowed_origins: tuple[str, ...] = _DEFAULT_CORS_ORIGINS
    allow_remote_bind: bool = False

    def __post_init__(self) -> None:
        if not self.database_url.strip():
            raise ValueError("database_url must not be empty")
        if not self.host.strip():
            raise ValueError("host must not be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if self.sse_poll_interval_seconds <= 0:
            raise ValueError("sse_poll_interval_seconds must be positive")
        if not self.allow_remote_bind and not _is_loopback_host(self.host):
            raise ValueError(
                "FRIDAY_API_HOST must be loopback unless FRIDAY_API_ALLOW_REMOTE_BIND=true"
            )
        for origin in self.cors_allowed_origins:
            _validate_origin(origin)

    @classmethod
    def from_env(cls) -> ApiSettings:
        raw_origins = os.environ.get("FRIDAY_API_CORS_ORIGINS")
        cors_allowed_origins = (
            tuple(origin.strip() for origin in raw_origins.split(",") if origin.strip())
            if raw_origins is not None
            else _DEFAULT_CORS_ORIGINS
        )
        return cls(
            database_url=os.environ.get("FRIDAY_API_DATABASE_URL", _DEFAULT_DATABASE_URL),
            host=os.environ.get("FRIDAY_API_HOST", _DEFAULT_HOST),
            port=_parse_int("FRIDAY_API_PORT", _DEFAULT_PORT),
            sse_poll_interval_seconds=_parse_float(
                "FRIDAY_API_SSE_POLL_INTERVAL_SECONDS", _DEFAULT_SSE_POLL_INTERVAL_SECONDS
            ),
            cors_allowed_origins=cors_allowed_origins,
            allow_remote_bind=_parse_bool("FRIDAY_API_ALLOW_REMOTE_BIND", False),
        )
