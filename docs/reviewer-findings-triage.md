# Reviewer Findings Triage

This document triages reviewer feedback against the current state of
`vllm-source-gateway`.

The goal is to separate:

- outdated findings that were true in older revisions
- issues that should be fixed immediately
- follow-up hardening work
- items that can be deferred without violating the current MVP boundary

This triage is based on:

- the current repository state
- existing tests
- one completed real-upstream E2E validation run
- the current deployment baseline being single process / single worker

## Current Contract

The current gateway contract is intentionally locked to a **single-process deployment baseline**.

Current baseline:

- one uvicorn worker per container
- in-memory routing state is process-local and accepted under that baseline
- in-memory upstream health state is process-local and accepted under that baseline
- in-memory Prometheus registry state is process-local and accepted under that baseline

Not part of the current contract:

- multi-worker safety
- shared-state coordination across workers
- horizontally scaled request distribution with consistent per-process metrics semantics

## Outdated

### `stream: true` directly returns `501`

This was true in an earlier revision but is no longer true.

Current state:

- streaming pass-through exists
- chat and responses streaming paths are tested
- one real-upstream E2E validation run completed successfully

### `stream_idle_seconds` is unused

This is no longer true.

Current state:

- streaming timeout wiring uses `httpx.Timeout(read=stream_idle_seconds, ...)`

### there is no client disconnect or cancel handling

This statement is too strong for the current codebase.

Current state:

- streaming path checks `request.is_disconnected()`
- streaming path handles `asyncio.CancelledError`

Still true:

- upstream cancellation behavior can be strengthened further

### `proxy.py` has no tests

This is no longer true.

Current state includes tests for:

- chat completions proxy
- responses proxy
- source resolution
- token accounting
- streaming pass-through behavior

## Completed Fix Now

### upstream health is no longer a stub

Current state:

- active upstream health polling exists
- routing health state can be updated from real probe results
- `/v1/models` can reflect health-derived availability

Result:

- stale all-healthy reporting is no longer the default behavior

### request-path proxying no longer creates a fresh `httpx.AsyncClient`

Current state:

- shared upstream HTTP clients are created in app lifespan
- request paths reuse those clients through dependency injection

Result:

- connection pooling and keep-alive reuse are part of the current baseline

### token accounting is gated on upstream `2xx` success

Current state:

- prompt and generation tokens are recorded only for successful upstream `2xx` responses
- non-2xx responses with `usage` fall back to conservative accounting behavior

Result:

- partial or error responses no longer violate the conservative token contract

### header forwarding is tightened

Current state:

- request forwarding now uses a bounded allowlist plus explicit blocked headers
- hop-by-hop headers are stripped explicitly
- response forwarding also strips unsafe downstream headers such as hop-by-hop fields and `content-encoding`

Result:

- proxy header behavior is materially safer and more predictable than the original blacklist-only approach

### unknown key behavior is now productized

Current state:

- default MVP behavior still attributes missing or unmapped keys to `department="unknown"`
- deployments can enable `security.reject_unknown_api_keys=true` to reject unknown callers at the ingress boundary

Result:

- this is now an explicit product/security choice instead of accidental fallback behavior

### latency histogram buckets are customized for LLM request ranges

Current state:

- request duration histogram uses explicit buckets from `0.1s` through `300s`
- the bucket range now covers common LLM latency distributions far better than the Prometheus default set

Result:

- `gateway_request_duration_seconds_bucket` is more useful for P95/P99-style latency analysis

### liveness and readiness are split

Current state:

- `/livez` provides process-level liveness
- `/readyz` reports readiness from current upstream health state
- `/healthz` remains as a backward-compatible liveness alias

Result:

- operational checks can now distinguish "process is up" from "gateway currently has healthy upstream capacity"

### request metrics are middleware-based

Current state:

- request metric finalization is centralized in middleware
- proxy code still records source resolution and token accounting, but no longer owns the primary request counter and duration lifecycle
- unexpected gateway exceptions can now be counted as request failures without needing route-local metrics wiring

Result:

- request metrics are less brittle and less dependent on every proxy branch remembering to call the counter manually

### request body size limits are enforced

Current state:

- JSON proxy endpoints enforce `server.max_request_body_bytes`
- oversized request bodies are rejected before JSON parsing with `413`
- both `Content-Length` precheck and actual body-size enforcement exist

Result:

- oversized or malicious request bodies no longer flow straight into unbounded `request.json()` parsing

### nested config models reject unknown fields

Current state:

- nested configuration models use `extra="forbid"`
- typoed nested config keys now fail config validation instead of being ignored

Result:

- configuration mistakes are caught earlier and more consistently

### API key lookup is precomputed

Current state:

- startup validation builds an `api_key -> department` reverse lookup map
- request-path source resolution now uses direct lookup instead of scanning all departments

Result:

- source resolution is simpler and avoids unnecessary linear scans on the hot path

### structured logging is now materially useful

Current state:

- application logs use JSON formatting instead of plain text `basicConfig` output
- request middleware emits a completion log with request method, path, endpoint, status, duration, and request or trace ids when present
- existing startup and upstream health logs now preserve `extra={...}` fields in emitted output

Result:

- request-level operational debugging is much more practical without introducing a separate logging stack

### single-process contract vs stateless wording is now clarified

Current state:

- the deployment baseline is now explicitly documented as single process / single worker per container
- README no longer implies that the current implementation already supports stateless horizontal scaling
- current in-memory routing, health, and metrics state are described as accepted constraints under that baseline

Result:

- the documented deployment contract is now aligned with the current implementation
- multi-worker or shared-state scaling is no longer implied as a present capability

### `/metrics` access boundary is now clarified

Current state:

- README now treats `/metrics` as an internal observability surface
- the deployment posture is explicitly internal Prometheus scrape or trusted internal network access
- stronger protection, if needed, is currently expected to be enforced at the deployment or network boundary

Result:

- the project no longer implies that unauthenticated public exposure of `/metrics` is an accepted production posture

### non-proxy route observability scope is now clarified

Current state:

- README now states that the bounded metrics contract is optimized for source-attributed proxy traffic
- `/v1/chat/completions` and `/v1/responses` are documented as the primary inputs for department-level request, token, and failure views
- `/v1/models`, `/livez`, `/readyz`, `/healthz`, `/metrics`, generic `404` responses, and some pre-proxy failures are explicitly documented as outside the current first-class source-attributed metrics contract

Result:

- the current observability contract is now explicit instead of implying full route-level coverage for every gateway endpoint

## Should Clarify Now

## Next Hardening Batch

### health probing is still serial

Reviewer observation:

- valid

Current state:

- upstream probes are awaited sequentially during one refresh cycle
- refresh latency scales linearly with upstream count and timeout duration

Recommended next action:

- switch one refresh cycle to concurrent probing with `asyncio.gather`

### same-model failover is still absent for connect-stage upstream failures

Reviewer observation:

- valid

Current state:

- a single connect or timeout failure still returns `502` or `504`
- the current proxy path does not attempt a same-model fallback to another healthy replica

Recommended next action:

- consider a narrowly scoped failover only for connect-stage or pre-response failures that are safe to retry
- keep this explicitly narrower than a general retry layer

### gateway-origin and upstream-origin failures are distinguished

Current state:

- the base request counter remains unchanged for compatibility
- `gateway_http_request_failures_total` adds a bounded `failure_origin` label for `4xx` and `5xx`
- gateway-synthesized failures are recorded as `failure_origin="gateway"`
- upstream non-2xx responses that are passed through are recorded as `failure_origin="upstream"`

Result:

- dashboards and alerts can now separate gateway failures from upstream failures without losing the existing request contract

### production secret handling is defined

Current state:

- departments can use `api_keys_from_env` instead of inline `api_keys`
- env-backed keys are resolved at startup and fail fast when missing or empty
- the config model allows local inline keys for development while giving production a secret reference path

Result:

- production deployments no longer need to store real department API keys directly in repo-tracked YAML files

### container runtime still runs as root

Reviewer observation:

- valid

Current state:

- the runtime container still executes as root

Recommended next action:

- add a non-root runtime user in the production container image

### container healthcheck is still missing

Reviewer observation:

- valid

Current state:

- Docker and Compose definitions do not yet declare a container-level healthcheck
- `/livez` and `/readyz` already exist and can back that check

Recommended next action:

- add a container healthcheck using `/livez` or `/readyz`

### SSE decode buffer still has no explicit upper bound

Reviewer observation:

- valid

Current state:

- streaming SSE parsing still allows unbounded decode-buffer growth if an upstream never terminates events correctly

Recommended next action:

- add a reasonable guardrail for decode-buffer growth as a targeted hardening step

### dead code and unreachable branches are cleaned up

Current state:

- obsolete `require_api_key` helper is removed
- unreachable non-`round_robin` routing branch is removed
- source-resolution and routing code now align more closely with the current product contract

Result:

- the codebase is less misleading and has fewer branches that imply unsupported behavior

## Valid but Defer

### env resolution inside Pydantic validation

Reviewer observation:

- valid as a design tradeoff

Why defer:

- current startup fail-fast behavior is intentional
- the current delivery path values runtime secret resolution simplicity over separating config lint from secret resolution
- config lint or dry-run without secrets is not yet a current requirement

Action:

- keep the current env-backed validation behavior unless standalone config validation becomes a concrete need

## Already Handled or Narrowed

### per-process routing, health, and metrics state

Reviewer observation:

- valid only as a blocker for multi-worker or multi-process deployment

Current state:

- routing round-robin state is process-local
- upstream health state is process-local
- metrics registry state is process-local

Result:

- under the current single-process baseline, these are known and accepted constraints
- they should not be treated as already solved for multi-worker deployment
- they become blocking only if the project adopts multi-worker or horizontally scaled shared-state operation

## Defer

### config hot reload

Why defer:

- restart-based config change is acceptable for the current MVP
- adding hot reload increases complexity quickly

Action:

- restart requirement is now documented; keep restart-based config changes unless hot reload becomes a deliberate feature

### `/v1/models` information exposure tuning

Why defer:

- current model list shape is acceptable for MVP
- replica-count exposure can be revisited once auth posture is clearer

### full stateful Responses API semantics

Why defer:

- current MVP can remain scoped to `POST /v1/responses`
- full stored-response and retrieval semantics are a larger compatibility surface

Action:

- current `POST /v1/responses` proxy-only scope is now documented; keep full stateful semantics out of the current MVP

## Suggested Next Order

Recommended next implementation order:

1. clarify single-process contract in docs
2. concurrent upstream health probing
3. connect-stage same-model failover
4. non-root container runtime
5. Docker or Compose healthcheck
6. observability scope clarification for non-proxy routes
7. optional SSE buffer bound hardening

## Assumptions

- This triage assumes the current repo state is the source of truth.
- This triage assumes one successful real-upstream E2E run is part of the current baseline.
- `reject_unknown_keys` should remain a decision point until the gateway's security boundary is explicitly defined.
- the gateway is intentionally locked to a single-process / single-worker deployment baseline for now
- `/metrics` is expected to be exposed only to internal Prometheus or trusted internal network paths
- multi-worker support is not part of the current contract
- horizontal scaling remains a future architecture topic, not a present guarantee
