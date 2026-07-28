"""Restricted HTTPS webhook transport for operator-owned routes."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from friday.application.delivery_dispatcher import (
    MessagingRoute,
    TransportOutcome,
    TransportOutcomeKind,
)
from friday.domain.outbound_delivery import OutboundDelivery

MAX_RESPONSE_BYTES = 8_192


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        del req, fp, code, msg, headers, newurl
        return None


class WebhookTransport:
    """POST one configured JSON field without following authority-changing redirects.

    Any exception raised after urllib begins the request is conservatively
    ambiguous.  The stdlib cannot prove whether bytes reached the peer, so a
    timeout or connection close must never be considered retry-safe.
    """

    def __init__(self) -> None:
        self._opener = build_opener(_NoRedirects())

    def deliver(self, route: MessagingRoute, delivery: OutboundDelivery) -> TransportOutcome:
        payload = json.dumps(
            {route.payload_field: delivery.body}, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        request = Request(
            route.endpoint,
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=route.timeout_seconds) as response:
                # Do not expose or persist remote content. A bounded read
                # merely ensures a misbehaving peer cannot retain a socket.
                response.read(MAX_RESPONSE_BYTES + 1)
                if 200 <= response.status < 300:
                    return TransportOutcome(TransportOutcomeKind.DELIVERED)
                return _ambiguous_http_status()
        except HTTPError:
            # HTTPError represents a real response, therefore dispatch did
            # begin. The remote service may already have made an effect.
            return _ambiguous_http_status()
        except (TimeoutError, URLError, OSError):
            return TransportOutcome(
                TransportOutcomeKind.AMBIGUOUS,
                failure_code="webhook_transport_uncertain",
                failure_message="webhook request outcome could not be determined",
            )


def _ambiguous_http_status() -> TransportOutcome:
    return TransportOutcome(
        TransportOutcomeKind.AMBIGUOUS,
        failure_code="webhook_response_unacceptable",
        failure_message="webhook response did not prove successful delivery",
    )
