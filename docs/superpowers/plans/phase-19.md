# Phase 19 — Messaging Gateway & Scheduled Delivery

## Status

**Proposed implementation design**

Dependency:

```text
Phase 16 — Durable Scheduled Automations ✅
Phase 17 — Conversation / Run Answer lifecycle ✅
Phase 18 — Friday-owned MCP & External Integrations ✅ APPROVED

Phase 19 — Messaging Gateway & Scheduled Delivery ← NEXT
```

Implementation branch after Phase 18 is merged into `main`:

```text
phase-19-messaging-gateway-scheduled-delivery
```

Do not build Phase 19 from the Phase 18 branch before PR #14 is merged. Start from fresh resolved `main`.

---

# 1. Goal

Phase 19 gives Friday a durable, policy-controlled way to communicate outward.

Friday should support:

```text
user / Claude
→ propose outbound message
→ Friday validates
→ approval
→ durable delivery intent
→ delivery worker
→ MessagingGateway
→ transport
→ external destination
```

and:

```text
Phase 16 Schedule
→ ScheduleFire
→ Run
→ final Friday answer
→ durable delivery intent
→ MessagingGateway
→ external destination
```

The key invariant is:

> Claude may produce message content, but Claude never owns a messaging transport, credential, recipient endpoint, retry decision, or scheduled delivery authority.

The Phase 18 principle remains unchanged:

```text
Claude proposes.
Friday authorizes.
Friday persists.
Friday executes.
Friday fences ambiguity.
```

---

# 2. Primary use cases

### Immediate outbound communication

Examples:

```text
"Send this update to my notification channel."
"Message me the deployment result."
"Send this summary to my personal Slack."
```

Claude proposes `message.send`.

The action requires exact approval.

---

### One-shot delayed communication

Example:

```text
"Send this message tomorrow at 09:00."
```

`message.send` may carry a future `deliver_at`.

Friday stores the exact approved message now and sends it later.

The model is not invoked again to reconstruct the message.

---

### Scheduled automation delivery

Example:

```text
Every day at 08:00:
    run my market summary
    deliver the final answer to personal.telegram
```

Phase 16 remains responsible for:

```text
cron
timezone
DST
missed occurrences
ScheduleFire
Run creation
retry lineage
```

Phase 19 only adds:

```text
ScheduleFire / execution
→ final answer
→ outbound delivery
```

There must not be another recurring cron engine.

---

# 3. Non-goals

Phase 19 should NOT implement:

- inbound Slack/Telegram/email commands;
- chat synchronization;
- mailbox ingestion;
- arbitrary HTTP requests;
- arbitrary webhook payload templates;
- arbitrary MCP tool invocation through messaging;
- attachments;
- media upload;
- message editing/deletion;
- reactions;
- read receipts;
- channel discovery;
- provider-specific thread semantics;
- autonomous contact lookup;
- multi-agent delegation;
- skills/self-modification.

Those belong to later phases or transport-specific extensions.

Phase 19 is about **safe outbound delivery**.

---

# 4. Architecture

## 4.1 High-level architecture

```text
                    ┌─────────────────────┐
                    │       Claude        │
                    │      brain only     │
                    └──────────┬──────────┘
                               │ proposal
                               ▼
                    ┌─────────────────────┐
                    │ Friday Tool Runtime │
                    │ risk + approval     │
                    │ claim fencing       │
                    └──────────┬──────────┘
                               │
                         message.send
                               │
                               ▼
                    ┌─────────────────────┐
                    │ OutboundDelivery    │
                    │ durable intent      │
                    └──────────┬──────────┘
                               │ claim
                               ▼
                    ┌─────────────────────┐
                    │ DeliveryDispatcher  │
                    └──────────┬──────────┘
                               │
                         fresh claim
                               │
                               ▼
                    ┌─────────────────────┐
                    │ MessagingGateway    │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ MessageTransport    │
                    │ webhook / future    │
                    └──────────┬──────────┘
                               │
                               ▼
                         external service
```

External I/O must never happen inside the transaction that claims or updates a delivery.

Use the same shape already proven by tool execution:

```text
Txn A
→ durable ownership / SENDING
→ commit

fresh claim check
→ external I/O

Txn B
→ verify ownership
→ persist outcome
→ commit
```

---

# 5. Important design decision: send asynchronously

`message.send` should **create a durable delivery intent**, not synchronously talk to Slack/email/etc.

Tool result:

```json
{
  "delivery_id": "...",
  "status": "queued"
}
```

not:

```json
{
  "status": "delivered"
}
```

unless actual delivery has completed.

This separates:

```text
authorization of an external communication
```

from:

```text
unreliable network delivery
```

and makes delayed delivery, retries, worker restart and crash recovery use the same architecture.

---

# 6. Domain model

## 6.1 OutboundDelivery

Add a durable aggregate:

```text
OutboundDelivery
```

Suggested fields:

```text
id
source_kind
source_run_id
source_tool_invocation_id
source_schedule_fire_id

route_id
route_fingerprint

subject
body
body_sha256

status
available_at

attempt_count

claim_owner
claim_token
claim_generation
claim_expires_at

provider_message_id

failure_code
failure_message

created_at
updated_at
delivered_at
```

`source_*` fields may be nullable according to source kind, but database CHECK constraints should enforce valid combinations.

Recommended source kinds:

```text
agent_request
scheduled_run_answer
```

---

## 6.2 Delivery states

```text
QUEUED
  │
  ▼
SENDING
  ├──────────────► DELIVERED
  │
  ├──────────────► FAILED
  │
  └──────────────► AMBIGUOUS

QUEUED ──────────► CANCELLED
```

### Meaning

**QUEUED**

Friday has durable authority to send this exact message but delivery has not begun.

**SENDING**

A worker owns the attempt. External side effect may be in progress.

**DELIVERED**

Transport returned definite success.

**FAILED**

Friday knows the message was not delivered, or a definite terminal policy/configuration failure occurred.

**AMBIGUOUS**

The external effect may have occurred but Friday cannot prove its outcome.

No automatic retry.

**CANCELLED**

Cancelled before dispatch.

---

# 7. Ambiguity rule

Messaging must inherit the Phase 18 conservative side-effect rule.

Once request dispatch could have begun:

```text
timeout
connection loss
worker crash
malformed response
unexpected remote response
claim loss after send
```

must never become:

```text
FAILED → retry automatically
```

unless the transport provides a verified idempotency guarantee.

Default:

```text
post-dispatch uncertainty
→ AMBIGUOUS
→ no automatic resend
```

This prevents:

```text
send
→ response lost
→ retry
→ duplicate message
```

---

# 8. Delivery claiming

Add a delivery-specific durable lease.

Required semantics should match the existing worker philosophy:

```text
worker A claims delivery
worker B cannot claim it

lease expires before dispatch
→ another worker may claim

lease expires after potential side effect
→ do NOT blindly resend
```

Recovery of abandoned `SENDING`:

```text
transport has proven idempotency
    → retry using same delivery idempotency key

otherwise
    → AMBIGUOUS
```

For Phase 19 MVP, assume transports **do not** have proven provider idempotency unless explicitly implemented and tested.

---

# 9. Messaging routes

Claude must never provide a URL, token, webhook secret, SMTP credential, or arbitrary recipient address.

Claude addresses only an operator-owned alias:

```text
personal.notifications
deployment.alerts
personal.slack
```

Example model-visible input:

```json
{
  "route": "personal.notifications",
  "body": "Deployment completed successfully."
}
```

Not:

```json
{
  "url": "https://hooks.slack.com/...",
  "token": "...",
  "channel": "..."
}
```

---

# 10. MessagingRoute configuration

Suggested configuration:

```text
MessagingRouteConfig
    route_id
    enabled
    trusted_description
    transport
    principal_id
    endpoint_env
    payload_field
    max_body_chars
    timeout_seconds
```

Example:

```json
{
  "route_id": "personal.notifications",
  "enabled": true,
  "trusted_description": "My personal notification channel",
  "transport": "webhook",
  "principal_id": "personal-notifications",
  "endpoint_env": "FRIDAY_PERSONAL_NOTIFICATION_WEBHOOK",
  "payload_field": "text"
}
```

`endpoint_env` contains the name of an environment variable.

The resolved URL is never persisted.

---

# 11. Route identity

Every route gets an immutable runtime fingerprint.

Conceptually:

```text
route fingerprint v1

route_id
transport kind
principal_id
resolved destination identity digest
payload shape
safe transport configuration
```

Canonical serialization only.

Never serialize with custom string separators.

Never persist:

```text
raw webhook URL
token
password
authorization header
```

If any authority-bearing route property changes:

```text
route fingerprint changes
```

An already queued delivery carrying the old fingerprint must fail closed before external I/O.

Example:

```text
queue delivery targeting endpoint A

operator changes endpoint to B

dispatcher resolves route
current fingerprint != frozen fingerprint

→ zero network request
→ route_binding_changed
```

---

# 12. First production transport

Recommended Phase 19 production transport:

## HTTPS Webhook Transport

Reasons:

- useful immediately for personal notifications;
- simple boundary;
- no provider SDK dependency;
- works behind Slack/Discord/custom notification relays;
- easily testable against a deterministic local HTTP fixture;
- does not require adding provider-specific domain concepts.

Strict scope:

```text
fixed operator-owned endpoint
POST
application/json
one configured top-level text field
bounded text
bounded response
bounded timeout
HTTPS in production
```

Do NOT add:

```text
arbitrary headers from Claude
arbitrary method
arbitrary URL
arbitrary JSON template
redirect-driven authority changes
```

Credentials/secret URLs come through environment references.

A local deterministic transport/HTTP fixture is mandatory for tests.

---

# 13. MessageToolGateway

Expose a Friday-owned tool:

```text
message.send
```

Input schema proposal:

```json
{
  "type": "object",
  "properties": {
    "route": {
      "type": "string"
    },
    "body": {
      "type": "string"
    },
    "subject": {
      "type": "string"
    },
    "deliver_at": {
      "type": "string"
    }
  },
  "required": ["route", "body"],
  "additionalProperties": false
}
```

`subject` may initially be supported only by transports that understand it.

`deliver_at`:

- RFC3339;
- explicit timezone/offset required;
- normalized to UTC;
- bounded future horizon;
- absent means immediately eligible;
- past timestamps rejected.

If subject support adds needless complexity for webhook MVP, omit it from the first commit and add it only when a transport needs it.

---

# 14. Risk policy

Add:

```text
ApprovalCategory.EXTERNAL_COMMUNICATION
```

`message.send` is always:

```text
read_only = false
approval_required = true
```

Risk scope:

```text
message:<route_fingerprint>
```

The normal authorization fingerprint additionally covers tool input, therefore approval binds:

```text
route
body
subject
deliver_at
route identity
```

Changing any one requires new approval.

No special messaging approval mechanism should bypass `ExecuteToolAction`.

---

# 15. Immediate send flow

```text
Claude
→ message.send(
      route="personal.notifications",
      body="build succeeded"
  )

MessageToolGateway.assess()
→ EXTERNAL_COMMUNICATION
→ approval required
→ authorization_scope=message:<route-fingerprint>

human approves

ExecuteToolAction Txn A
→ consume exact approval
→ persist ToolInvocation RUNNING
→ commit

fresh claim check

MessageToolGateway.execute()
→ validate route
→ validate body
→ CreateOutboundDelivery(
     source_tool_invocation_id=<invocation>
   )
→ durable QUEUED row
→ commit

ExecuteToolAction Txn B
→ ToolInvocation SUCCEEDED
→ output:
  {
    delivery_id,
    status: "queued"
  }
```

Database uniqueness:

```text
UNIQUE(source_tool_invocation_id)
```

ensures ToolInvocation replay cannot enqueue a second message.

---

# 16. Delivery worker flow

```text
DeliveryDispatcher
↓
list due QUEUED deliveries
↓
claim one
↓
Txn A:
    verify route still exists
    verify route fingerprint
    mark SENDING
    persist attempt
    commit
↓
fresh delivery claim verification
↓
MessagingGateway.deliver()
↓
NO DATABASE TRANSACTION OPEN
↓
Txn B:
    verify same delivery claim
    persist DELIVERED / FAILED
    commit
```

If outcome is uncertain:

```text
leave enough durable evidence
→ AMBIGUOUS
→ never auto resend
```

If stale worker loses its claim after network I/O, it may not persist success.

---

# 17. Retry policy

Only definite pre-dispatch failures may auto retry.

Example:

```text
endpoint temporarily unavailable before any request can be dispatched
→ retryable
→ exponential backoff
→ QUEUED with new available_at
```

Post-dispatch uncertainty:

```text
→ AMBIGUOUS
```

Recommended bounds:

```text
max attempts: 5
base delay: 5 s
multiplier: 2
max delay: 5 min
```

Use existing retry abstractions if they fit; do not invent a second subtly different backoff implementation unnecessarily.

---

# 18. Scheduled automation delivery

This is the most important Phase 16 integration.

Existing Phase 16 owns:

```text
Schedule
ScheduleFire
Run
execution lineage
recurrence
DST
coalescing
```

Phase 19 adds an optional delivery binding.

Suggested durable model:

```text
ScheduleDeliveryPolicy
    schedule_id
    route_id
    route_fingerprint
    enabled
    created_at
    updated_at
```

Do not put credentials in this table.

---

# 19. Freeze delivery authority at ScheduleFire

A schedule configuration may change while its Run is executing.

Therefore a due occurrence must snapshot its delivery authority.

Recommended model:

```text
ScheduleFireDeliveryPlan
    schedule_fire_id
    execution_id
    route_id
    route_fingerprint
    created_at
```

Creation should happen in the same atomic materialization transaction as:

```text
ScheduleFire
Run
```

Conceptually:

```text
due Schedule
↓
materialize occurrence

Txn:
    create Run
    create ScheduleFire
    snapshot delivery plan
    advance Schedule
commit
```

A later schedule edit affects only future fires.

---

# 20. Retry lineage semantics

Never deliver an answer merely because the first Run attempt failed or ended.

Delivery is attached to the **execution lineage**, not blindly to the first Run ID.

Example:

```text
ScheduleFire
↓
Run A
↓ retryable failure
Run B
↓ retry
Run C
↓ success + final answer
```

Expected:

```text
Run A → zero delivery
Run B → zero delivery
Run C final answer → exactly one delivery
```

The `ScheduleFireDeliveryPlan` should resolve the canonical terminal successful execution result.

---

# 21. Turning a run answer into a delivery

Once an execution has:

```text
terminal success
+
canonical final answer
```

Friday materializes:

```text
OutboundDelivery(
    source_kind=scheduled_run_answer,
    source_schedule_fire_id=...,
    route_id=frozen route,
    route_fingerprint=frozen fingerprint,
    body=canonical final answer
)
```

Database uniqueness:

```text
UNIQUE(source_schedule_fire_id, route_id)
```

or equivalent logical identity.

Running the finalization hook twice must create one row.

---

# 22. Failed scheduled executions

Phase 19 default:

```text
scheduled run succeeds
→ deliver final answer

scheduled run terminally fails
→ no outbound message
```

Failure alerts are useful, but make them a later extension rather than mixing two semantics into the first implementation.

---

# 23. Scheduled content authority

Automatic delivery is a standing authority granted to:

```text
this exact Schedule
→ this exact configured route
→ the final output of this ScheduleFire execution
```

It is NOT authority for Claude to call `message.send` without approval.

Enforce source identity:

```text
ScheduleDeliveryPolicy
→ ScheduleFireDeliveryPlan
→ execution lineage
→ canonical final answer
```

Claude cannot substitute another Run's output.

---

# 24. Scheduled content safety gate

Future generated content was not known when the schedule was created.

Before automatically externalizing it, apply deterministic Friday-owned checks:

```text
valid text
bounded size
no binary data
no transport credentials
secret-shape screening
existing sensitivity policy where applicable
```

Do not ask Claude whether its own answer is safe to externalize.

A policy rejection:

```text
→ zero network
→ stable failure code
→ delivery FAILED/BLOCKED
```

Do not silently truncate sensitive content into something potentially misleading.

Ordinary oversized non-sensitive content may either fail closed or use an explicitly documented deterministic truncation policy.

Prefer fail closed for Phase 19.

---

# 25. Schedule API/UI extension

Phase 16 schedule creation currently controls timing.

Extend schedule API with optional:

```text
delivery_route_id
```

Example conceptual request:

```json
{
  "kind": "cron",
  "cron": "0 8 * * *",
  "timezone": "Asia/Ho_Chi_Minh",
  "delivery_route_id": "personal.notifications"
}
```

Schedule detail should expose only safe delivery metadata:

```text
route_id
trusted description
enabled
```

Never endpoint/token values.

Changing route binding should affect future `ScheduleFire`s only.

---

# 26. Delivery API

Add read/control endpoints roughly equivalent to:

```text
GET /deliveries/:id
GET /runs/:run_id/deliveries
GET /schedules/:schedule_id/deliveries

POST /deliveries/:id/cancel
```

Cancellation only succeeds while side effect definitely has not started:

```text
QUEUED → CANCELLED
```

For:

```text
SENDING
DELIVERED
AMBIGUOUS
```

cancel must fail closed.

Do not expose "retry ambiguous" as a single-click automatic action in Phase 19.

---

# 27. Operational health

Provide safe route health:

```text
route_id
enabled
transport
status
last_success_at
failure_code
```

Never:

```text
URL
token
headers
body
credential hash
```

Delivery metrics/logging should include:

```text
delivery_id
route_id
source_kind
attempt
state
failure_code
latency
```

Do not log message body by default.

---

# 28. Configuration bounds

Fail startup for unsafe operator configuration.

Suggested global bounds:

```text
max routes                  32
max route id chars          64
max body chars              16,000
max subject chars           512
max config bytes            1 MB
max future deliver_at       365 days
transport timeout           <= 60 s
max attempts                <= 10
```

All numbers should be centralized constants/config, not scattered magic values.

Reject:

```text
duplicate route IDs
normalization collisions
unknown config keys
duplicate JSON keys
NaN/Infinity
missing env references
unsafe transport
invalid payload field
invalid timeout
```

---

# 29. Disabled behavior

Messaging should be opt-in and disabled by default.

When disabled:

```text
zero transport construction
zero network
zero route discovery
zero delivery polling
message.send absent from tool manifest
```

Existing Friday behavior remains unchanged.

---

# 30. Security boundaries

## Claude must never receive

```text
webhook URL
API token
Authorization header
credential environment value
internal transport errors
remote response bodies
```

## Claude may receive

```text
route alias
trusted operator description
delivery id
safe delivery state
stable failure code
```

Remote HTTP/service response bodies are untrusted and unnecessary for Phase 19; discard them after bounded protocol handling.

---

# 31. Acceptance criteria

## AC19-01 — Authority isolation

```text
messaging disabled
→ message.send absent
→ zero transport construction

unconfigured route
→ rejected
→ zero delivery
→ zero network

configured route
→ only route alias/model-safe description exposed
```

---

## AC19-02 — Exact approval

Before approval:

```text
message.send
→ approval_required
→ zero OutboundDelivery
→ zero network
```

After approval:

```text
→ exactly one OutboundDelivery
```

Changing:

```text
route
body
subject
deliver_at
route fingerprint
```

must invalidate the approval.

---

## AC19-03 — Durable enqueue replay

```text
approved message.send
→ OutboundDelivery A

same ToolInvocation replay
→ reuse prior result
→ still only OutboundDelivery A
```

No duplicate durable send intent.

---

## AC19-04 — Future delivery

For:

```text
deliver_at = T+1h
```

dispatcher at:

```text
T+59m
→ zero network

T+1h
→ eligible
```

Restart before `deliver_at` must not lose the message.

---

## AC19-05 — Two-worker race

Two delivery workers race for one delivery:

```text
worker A claims
worker B loses
```

Exactly one transport call.

---

## AC19-06 — Route rebinding

Queue message against route identity A.

Change endpoint/principal/config to identity B before send.

Expected:

```text
zero request to B
delivery not silently retargeted
stable route_binding_changed failure
```

---

## AC19-07 — Secret safety

Fixture credential appears in:

```text
transport error
response
URL
headers
```

Assert raw value absent from:

```text
OutboundDelivery
DeliveryAttempt
events
logs
API responses
runtime context
ToolInvocation
```

---

## AC19-08 — Definite pre-dispatch failure

Simulate failure proven before dispatch.

Expected:

```text
retryable
→ bounded backoff
→ attempts increment
→ eventual success possible
```

---

## AC19-09 — Post-dispatch timeout

Fixture receives message, stores effect, then never replies.

Expected:

```text
external effect count = 1
delivery = AMBIGUOUS
automatic retry count = 0
```

---

## AC19-10 — Worker death after external effect

```text
claim delivery
→ send reaches fixture
→ kill worker before Txn B
→ lease expires
→ restart dispatcher
```

Expected:

```text
effect count = 1
no blind resend
delivery recovered as AMBIGUOUS
```

unless the tested adapter proves provider idempotency.

---

## AC19-11 — Claim loss before send

Lose claim after Txn A but before transport call.

Expected:

```text
zero network
stale worker cannot send
```

---

## AC19-12 — Claim loss after send

Send succeeds externally, then claim is lost before persistence.

Expected:

```text
stale worker cannot write DELIVERED
no automatic second send
recovery enters ambiguity semantics
```

---

## AC19-13 — Scheduled automation happy path

```text
Schedule due
→ exactly one ScheduleFire
→ exactly one execution
→ final successful answer
→ exactly one OutboundDelivery
→ exactly one transport effect
```

---

## AC19-14 — Scheduler race

Two Phase 16 scheduler actors materialize the same occurrence.

Expected:

```text
1 ScheduleFire
1 execution lineage
1 delivery plan
1 outbound delivery
1 external send
```

---

## AC19-15 — Retry lineage

```text
scheduled Run A fails retryably
Run B succeeds
```

Expected:

```text
Run A output → never delivered
Run B canonical final answer → delivered once
```

---

## AC19-16 — Scheduled terminal failure

Execution lineage ends FAILED.

Expected:

```text
zero outbound delivery
zero network
```

---

## AC19-17 — Schedule pause/cancel

Paused or cancelled Schedule:

```text
no new ScheduleFire
→ no new delivery plan
→ no message
```

Existing already-created delivery semantics remain explicit and tested; cancelling a Schedule should not retroactively cancel a delivery already in `SENDING`.

---

## AC19-18 — Schedule route update

Occurrence A fires with route A.

Operator changes schedule to route B while Run A executes.

Expected:

```text
occurrence A → frozen route A
future occurrence B → route B
```

No mid-flight retargeting.

---

## AC19-19 — Scheduled secret policy

Final answer contains a value recognized by Friday's deterministic sensitivity/secret policy.

Expected:

```text
delivery blocked
zero network
raw secret absent from logs/failure/context
```

---

## AC19-20 — Downtime recovery

Friday offline across several cron occurrences.

Phase 16 coalescing chooses the normal durable occurrence behavior.

Phase 19 must produce delivery only for the execution actually materialized by Phase 16:

```text
no backlog explosion
no duplicate messages
```

---

## AC19-21 — Existing schedules unaffected

Schedule without `delivery_route_id`:

```text
behavior identical to Phase 16
```

This is a mandatory regression gate.

---

## AC19-22 — Existing ToolGateway unaffected

Workspace, memory, computer and MCP tools retain identical:

```text
manifest
authorization
replay
claim fencing
```

Messaging must compose as another Friday-owned gateway rather than modifying their execution semantics.

---

# 32. Test plan

## Domain tests

Test:

```text
OutboundDelivery transitions
invalid transitions
timestamps
bounds
cancel semantics
ambiguous terminal behavior
```

---

## Route/config tests

Test:

```text
default disabled
duplicate route
normalization collision
unknown key
missing env
secret not persisted
max route count
config byte ceiling
timeouts
HTTPS policy
```

---

## Authorization tests

Test:

```text
body A approval != body B
route A approval != route B
time A approval != time B
binding A approval != binding B
```

---

## Persistence tests

Real SQLite:

```text
delivery round trip
claim lease
claim generation
state transition
unique source invocation
unique schedule-fire delivery
migration parity
concurrent claim fence
```

---

## Transport tests

Use deterministic local HTTP server.

Behaviors:

```text
success
connection failure before dispatch
effect_then_200
effect_then_hang
effect_then_connection_close
oversized response
malformed response
slow response
secret in response
redirect
```

Verify Friday's external-effect classification.

---

## Immediate-send E2E

Real:

```text
ExecuteToolAction
SQLite
MessageToolGateway
DeliveryDispatcher
WebhookTransport
local HTTP fixture
```

Prove:

```text
approval
→ queue
→ claim
→ send
→ delivered
```

and exactly one HTTP effect.

---

## Ambiguity E2E

Real fixture:

```text
receive body
increment durable effect counter
hang
```

Assert:

```text
delivery AMBIGUOUS
counter = 1
restart
counter still = 1
```

---

## Scheduled-delivery E2E

Real:

```text
Schedule
MaterializeDueSchedules
ScheduleFire
Run/retry lineage
final answer
delivery plan
OutboundDelivery
DeliveryDispatcher
HTTP fixture
```

This is the Phase 19 sign-off test.

---

## Security tests

Search exact fixture secret through:

```text
DB
events
operational logs
ToolInvocation
route health
API JSON
runtime context
exception strings
```

Expect zero raw occurrences.

---

## Worker composition tests

Failure after transport creation:

```text
→ every acquired resource closed
```

Messaging disabled:

```text
→ no transport construction
```

Delivery worker shutdown:

```text
→ no leaked thread/process/socket
```

---

## API/UI tests

At minimum:

```text
schedule route selection
schedule detail displays safe route alias
delivery status visible
pending delivery cancellation
no credentials returned
```

Browser E2E should cover one scheduled automation with a delivery status transitioning to delivered using a deterministic backend.

---

# 33. Recommended file structure

```text
src/friday/domain/
    outbound_delivery.py

src/friday/application/
    delivery_lifecycle.py
    delivery_dispatcher.py
    delivery_policy.py
    scheduled_delivery.py

src/friday/infrastructure/messaging/
    config.py
    gateway.py
    webhook_transport.py
    redaction.py

src/friday/infrastructure/persistence/
    existing files extended

apps/worker/
    messaging_settings.py
    app.py
    worker_loop.py

apps/api/
    routes/deliveries.py
    schemas/deliveries.py

docs/architecture/
    messaging-and-scheduled-delivery.md
```

Avoid creating abstractions such as `messaging/utils.py` containing mixed responsibilities.

---

# 34. Implementation order

### Step 1 — Design + domain

Implement:

```text
OutboundDelivery
states
route identity
ports
migration
repositories
```

No network yet.

### Step 2 — Durable delivery claims

Implement:

```text
due query
lease claim
generation
recovery
two-worker race tests
```

### Step 3 — MessageToolGateway

Implement:

```text
message.send
risk
approval scope
durable enqueue
```

Still no real network needed.

### Step 4 — MessagingGateway + fixture transport

Implement:

```text
DeliveryDispatcher
transport seam
definite vs ambiguous failures
```

### Step 5 — HTTPS webhook transport

Add production opt-in route implementation and secret/config hardening.

### Step 6 — Phase 16 bridge

Implement:

```text
ScheduleDeliveryPolicy
ScheduleFireDeliveryPlan
final execution answer → OutboundDelivery
```

### Step 7 — API/UI/operations

Expose safe status/control and operational health.

### Step 8 — Full adversarial proof suite

Race, restart, crash, ambiguity, secret, schedule retry lineage and exact-head CI.

---

# 35. Definition of Done

Phase 19 is APPROVED only when all of these are true:

```text
Claude has no transport authority
unconfigured destinations impossible
immediate send requires exact approval
scheduled delivery has durable standing authority
message content and route identity are frozen
no second recurring scheduler exists
no DB transaction surrounds network I/O
two workers cannot duplicate a send
restart cannot lose queued delivery
post-dispatch uncertainty never blindly retries
stale workers cannot persist outcomes
route changes cannot retarget old messages
credentials never reach durable/model-visible data
scheduled retry lineages deliver once
Phase 16 schedule behavior does not regress
existing ToolGateway behavior does not regress
messaging disabled means zero external activity
```

Mandatory gates:

```text
pre-commit
mypy
full pytest + coverage
migration/schema parity
architecture/policy tests
browser E2E
dependency audit
clean worktree
exact-head GitHub Actions
```

And the sign-off E2E must prove:

```text
cron Schedule
→ ScheduleFire
→ Run
→ retry if necessary
→ canonical final answer
→ durable OutboundDelivery
→ claim
→ real local transport
→ exactly one external effect
```

plus:

```text
external effect happens
→ response lost
→ AMBIGUOUS
→ restart
→ no second external effect
```

Those two tests are the core Phase 19 acceptance gate.
