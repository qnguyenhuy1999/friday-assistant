from __future__ import annotations

from email.message import Message
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from friday.infrastructure.messaging.config import MessagingRoute
from friday.infrastructure.messaging.transport_models import TransportOutcome, TransportRequest
from friday.infrastructure.messaging.webhook_transport import WebhookTransport


def _request() -> TransportRequest:
    return TransportRequest(
        MessagingRoute(
            "ops.alerts",
            "Ops",
            True,
            "https_webhook",
            "ops",
            "ENDPOINT",
            "https://hook.example.test/secret",
            "message",
            1_000,
            2.0,
        ),
        "private payload",
    )


class _Response:
    def __init__(self, status: int) -> None:
        self._status = status

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def getcode(self) -> int:
        return self._status


def test_posts_json_body_with_only_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def open_request(request: Request, timeout: float) -> _Response:
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response(204)

    class Opener:
        open = staticmethod(open_request)

    result = WebhookTransport(Opener()).send(_request())

    request = cast(Request, captured["request"])
    assert result.outcome is TransportOutcome.DELIVERED
    assert captured["timeout"] == 2.0
    assert request.get_method() == "POST"
    assert request.data == b'{"message":"private payload"}'
    assert dict(request.header_items()) == {"Content-type": "application/json"}


@pytest.mark.parametrize(
    ("error", "code", "outcome"),
    [
        (
            HTTPError("https://x", 404, "no", Message(), None),
            "webhook_http_4xx",
            TransportOutcome.FAILED,
        ),
        (
            HTTPError("https://x", 503, "no", Message(), None),
            "webhook_http_5xx",
            TransportOutcome.FAILED,
        ),
        (TimeoutError(), "webhook_timeout", TransportOutcome.AMBIGUOUS),
        (URLError("connection refused"), "webhook_connection_error", TransportOutcome.AMBIGUOUS),
        (URLError(TimeoutError()), "webhook_timeout", TransportOutcome.AMBIGUOUS),
    ],
)
def test_expected_webhook_failures_are_mapped_without_details(
    monkeypatch: pytest.MonkeyPatch, error: Exception, code: str, outcome: TransportOutcome
) -> None:
    def fail(*_: object, **__: object) -> _Response:
        raise error

    class Opener:
        open = staticmethod(fail)

    result = WebhookTransport(Opener()).send(_request())
    assert result.outcome is outcome
    assert result.failure_code == code
