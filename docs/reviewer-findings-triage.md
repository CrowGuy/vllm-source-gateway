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

## Fix Now

### upstream health is still a stub

Current state:

- upstream health defaults to in-memory `True`
- there is no active health polling
- there is no passive health downgrade on repeated failure

Why it matters:

- `/v1/models` can report stale availability
- routing can continue sending traffic to broken upstreams
- `NoHealthyUpstreamError` is not meaningful under real failure conditions

Recommended direction:

- add a real upstream health policy
- let routing and model discovery reflect actual health state

### each request creates a new `httpx.AsyncClient`

Current state:

- non-streaming path creates a fresh client per request
- streaming path also creates a fresh client per request

Why it matters:

- no shared connection pool
- extra TCP/TLS overhead
- throughput and latency degrade under concurrency

Recommended direction:

- create one shared async client in lifespan
- store it in `app.state`
- close it on shutdown

### token accounting is not gated on 2xx upstream success

Current state:

- token recording depends on `usage`
- it does not first require a successful upstream status

Why it matters:

- non-2xx responses with partial usage can violate conservative accounting semantics

Recommended direction:

- record prompt/generation tokens only for 2xx responses

### header forwarding is too permissive

Current state:

- request header forwarding uses a small blacklist
- downstream response filtering only strips a few hop-by-hop headers

Why it matters:

- RFC hop-by-hop behavior is not handled rigorously
- encoding or cookie behavior can become brittle
- per-upstream auth injection has no clear extension point

Recommended direction:

- move toward a whitelist-based forwarding policy
- strip hop-by-hop headers explicitly
- design per-upstream auth injection

### unknown key behavior is not productized

Current state:

- unknown or missing API keys fall through to `department="unknown"`
- request proxying still succeeds

Why it matters:

- acceptable for an internal attribution layer
- unsafe if the gateway becomes an external ingress boundary

Recommended direction:

- make this an explicit product/security decision
- add a configurable `reject_unknown_keys` mode if the gateway is used as an auth boundary

## Next Hardening Batch

### add middleware-based request metrics

Why:

- reduces manual metrics wiring
- protects against uncounted unexpected 500s
- centralizes duration and status accounting

### customize latency histogram buckets

Why:

- default histogram buckets do not fit typical LLM latency ranges
- 10s+ requests lose useful percentile resolution

Suggested direction:

- define buckets suitable for 0.1s through 120s or higher

### split liveness and readiness

Why:

- current `/healthz` only proves the process is up
- it does not express whether usable upstreams exist

Recommended direction:

- keep liveness simple
- derive readiness from real upstream health state

### add request body size limits

Why:

- current `request.json()` reads the full body into memory
- large or malicious bodies can waste memory

### improve structured logging

Why:

- current `logging.basicConfig` does not make good use of `extra=...`
- request-level operational debugging is still weak

### add `extra="forbid"` to nested config models

Why:

- typoed nested config fields can be silently ignored
- this is a high-value correctness hardening change

### precompute API key lookup

Why:

- current source resolution scans all departments linearly
- current scale is small, but a reverse lookup map is simpler and cheaper

### revisit round-robin behavior when health state changes

Why:

- current selection rotates over the filtered healthy list
- distribution can skew when health membership changes frequently

### distinguish gateway-origin vs upstream-origin failures in metrics

Why:

- current request counters flatten both into the same status-class view
- later observability will likely want to distinguish them

### define production secret handling

Why:

- example YAML keys are fine as examples
- production deployments should support env or secret reference patterns

## Defer

### config hot reload

Why defer:

- restart-based config change is acceptable for the current MVP
- adding hot reload increases complexity quickly

Action:

- document restart requirement clearly

### `/v1/models` information exposure tuning

Why defer:

- current model list shape is acceptable for MVP
- replica-count exposure can be revisited once auth posture is clearer

### full stateful Responses API semantics

Why defer:

- current MVP can remain scoped to `POST /v1/responses`
- full stored-response and retrieval semantics are a larger compatibility surface

Action:

- document the current scope explicitly

### dead code and unreachable branch cleanup

Examples:

- `require_api_key`
- repeated API key extraction patterns
- unreachable non-round-robin strategy handling

Why defer:

- worth cleaning
- not as urgent as health, client reuse, auth posture, or proxy correctness

## Suggested Next Order

Recommended next implementation order:

1. upstream health policy
2. shared `AsyncClient`
3. 2xx-only token accounting
4. header forwarding hardening
5. metrics middleware, readiness, and histogram improvements

## Assumptions

- This triage assumes the current repo state is the source of truth.
- This triage assumes one successful real-upstream E2E run is part of the current baseline.
- `reject_unknown_keys` should remain a decision point until the gateway's security boundary is explicitly defined.
