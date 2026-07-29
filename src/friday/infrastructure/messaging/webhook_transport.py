"""HTTPS-only webhook transport with deliberately sparse error reporting."""

from __future__ import annotations

import json
from types import TracebackType
from typing import Protocol, Self
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from friday.infrastructure.messaging.transport_models import TransportRequest, TransportResult


class _WebhookResponse(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def getcode(self) -> int: ...


class _WebhookOpener(Protocol):
    def open(self, request: Request, timeout: float) -> _WebhookResponse: ...


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *_: object, **__: object) -> None:
        return None


class WebhookTransport:
    """Perform one HTTPS JSON POST without exposing destination or payload."""

    def __init__(self, opener: _WebhookOpener | None = None) -> None:
        self._opener = opener or build_opener(_NoRedirects())

    def send(self, request: TransportRequest) -> TransportResult:
        encoded = json.dumps(
            {request.route.payload_field: request.body}, separators=(",", ":")
        ).encode("utf-8")
        http_request = Request(
            request.route.endpoint,
            data=encoded,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(http_request, timeout=request.route.timeout_seconds) as response:
                status = response.getcode()
        except HTTPError as exc:
            if 400 <= exc.code < 500:
                return TransportResult.failed("webhook_http_4xx")
            if 500 <= exc.code < 600:
                return TransportResult.failed("webhook_http_5xx")
            return TransportResult.failed("webhook_http_unexpected_response")
        except TimeoutError:
            return TransportResult.failed("webhook_timeout")
        except OSError:
            return TransportResult.failed("webhook_connection_error")
        except Exception:  # noqa: BLE001 - transport must not leak exception text
            return TransportResult.failed("webhook_transport_error")
        if 200 <= status < 300:
            return TransportResult.delivered()
        if 400 <= status < 500:
            return TransportResult.failed("webhook_http_4xx")
        if 500 <= status < 600:
            return TransportResult.failed("webhook_http_5xx")
        return TransportResult.failed("webhook_http_unexpected_response")
