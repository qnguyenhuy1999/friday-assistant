from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

from friday.application.delivery_dispatcher import TransportOutcomeKind
from friday.domain.identifiers import DeliveryId, RunId, ToolInvocationId
from friday.domain.outbound_delivery import DeliverySourceKind, OutboundDelivery
from friday.infrastructure.messaging.config import MessagingRoute
from friday.infrastructure.messaging.webhook_transport import WebhookTransport

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _Fixture:
    def __init__(
        self, *, status: int = 200, hang_seconds: float = 0, response: bytes = b"{}"
    ) -> None:
        self.status = status
        self.hang_seconds = hang_seconds
        self.response = response
        self.effects: list[bytes] = []

    def handler(self) -> type[BaseHTTPRequestHandler]:
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                fixture.effects.append(self.rfile.read(int(self.headers["Content-Length"])))
                if fixture.hang_seconds:
                    time.sleep(fixture.hang_seconds)
                self.send_response(fixture.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(fixture.response)))
                self.end_headers()
                self.wfile.write(fixture.response)

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        return Handler


@contextmanager
def _server(fixture: _Fixture) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), fixture.handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = cast(str, server.server_address[0])
        port = server.server_address[1]
        yield f"http://{host}:{port}/webhook"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _delivery() -> OutboundDelivery:
    return OutboundDelivery.new(
        id=DeliveryId.new(),
        source_kind=DeliverySourceKind.AGENT_REQUEST,
        source_run_id=RunId.new(),
        source_tool_invocation_id=ToolInvocationId.new(),
        route_id="test.notifications",
        route_fingerprint="a" * 64,
        body="hello webhook",
        available_at=NOW,
        created_at=NOW,
    )


def _route(endpoint: str, *, timeout: float = 1) -> MessagingRoute:
    return MessagingRoute(
        route_id="test.notifications",
        trusted_description="test fixture",
        principal_id="test",
        endpoint=endpoint,
        timeout_seconds=timeout,
        allow_insecure_for_tests=True,
    )


def test_webhook_success_proves_delivery_and_uses_fixed_payload_shape() -> None:
    fixture = _Fixture()
    with _server(fixture) as endpoint:
        outcome = WebhookTransport().deliver(_route(endpoint), _delivery())

    assert outcome.kind is TransportOutcomeKind.DELIVERED
    assert [json.loads(value) for value in fixture.effects] == [{"text": "hello webhook"}]


def test_webhook_response_failure_is_ambiguous_after_one_external_effect() -> None:
    fixture = _Fixture(status=500, response=b"credential=fixture-secret")
    with _server(fixture) as endpoint:
        outcome = WebhookTransport().deliver(_route(endpoint), _delivery())

    assert outcome.kind is TransportOutcomeKind.AMBIGUOUS
    assert outcome.failure_code == "webhook_response_unacceptable"
    assert "fixture-secret" not in repr(outcome)
    assert len(fixture.effects) == 1


def test_webhook_timeout_after_effect_is_ambiguous() -> None:
    fixture = _Fixture(hang_seconds=0.1)
    with _server(fixture) as endpoint:
        outcome = WebhookTransport().deliver(_route(endpoint, timeout=0.01), _delivery())

    assert outcome.kind is TransportOutcomeKind.AMBIGUOUS
    assert len(fixture.effects) == 1
