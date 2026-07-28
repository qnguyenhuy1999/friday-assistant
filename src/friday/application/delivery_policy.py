"""Deterministic, Friday-owned safety gate for automatic delivery."""

from __future__ import annotations

from friday.application.secret_shapes import contains_secret_shape

MAX_SCHEDULED_DELIVERY_BODY_CHARS = 16_000
BLOCKED_CONTENT_PLACEHOLDER = "[scheduled content blocked by Friday safety policy]"


def scheduled_delivery_rejection(body: str) -> str | None:
    """Return a stable, non-content-bearing rejection code when unsafe."""
    if not body.strip():
        return "scheduled_content_invalid"
    if len(body) > MAX_SCHEDULED_DELIVERY_BODY_CHARS:
        return "scheduled_content_too_large"
    if contains_secret_shape(body):
        return "scheduled_content_secret_detected"
    return None
