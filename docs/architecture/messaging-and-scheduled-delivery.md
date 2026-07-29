# Messaging and scheduled delivery

Phase 19 will make outbound messaging a durable workflow: an approved message intent is bound to a route, persisted, dispatched through a transport, and recorded with an unambiguous outcome. Scheduled runs may later create delivery intents through an explicit policy and fire-plan bridge.

## Implemented: Step 1

Step 1 implements only the durable substrate. `OutboundDelivery` records immutable authority, source identity, route binding, and message content; its lifecycle supports `queued → sending`, `sending → delivered|failed|ambiguous`, and `queued → cancelled`. The `outbound_deliveries` schema, repository, and unit-of-work wiring preserve those records and enforce one delivery per tool invocation or schedule fire.

## Deferred Phase 19 work

Transport adapters, `message.send`, approvals, dispatching, claims/recovery orchestration, retry or requeue policy, scheduled-delivery policy/fire-plan bridging, worker polling, API/UI/SDK surfaces, and webhook delivery are later steps. Step 1 does not send messages or materialize scheduled deliveries.
