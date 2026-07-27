from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

McpServerStatus = Literal["available", "degraded", "unavailable"]


@dataclass(frozen=True, slots=True)
class McpServerHealth:
    server_id: str
    status: McpServerStatus
    configured_binding_count: int
    available_binding_count: int
    last_success: datetime | None = None
    failure_code: str | None = None
