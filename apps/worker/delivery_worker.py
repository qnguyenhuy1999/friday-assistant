"""Worker adapter for the outbound dispatcher."""

from __future__ import annotations

from friday.infrastructure.messaging.dispatcher import DeliveryDispatcher, DispatchResult


class DeliveryWorker:
    def __init__(self, dispatcher: DeliveryDispatcher) -> None:
        self._dispatcher = dispatcher

    def run_once(self) -> bool:
        return self._dispatcher.dispatch_once() is not DispatchResult.IDLE
