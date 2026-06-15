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

## Next Hardening Batch

### improve structured logging

Why:

- current `logging.basicConfig` does not make good use of `extra=...`
- request-level operational debugging is still weak

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
