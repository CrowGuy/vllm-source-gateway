# MVP Task List

## Purpose

This document turns the current MVP decisions in [README.md](/home/randy/Documents/crow/vllm-source-gateway/README.md) into an implementation-oriented task list for `vllm-source-gateway`.

The goal is to provide a practical development sequence for:

- read-only model discovery
- API-key-based department attribution
- request forwarding to vLLM
- model-aware round-robin routing
- Prometheus-safe metrics
- conservative token accounting

## Assumptions

- MVP supports `POST /v1/chat/completions`
- MVP supports `GET /v1/models`
- MVP supports `POST /v1/responses`
- MVP supports streaming pass-through for chat completions
- source resolution is `API key -> department -> unknown`
- routing is model-aware round-robin across healthy upstreams
- token accounting is conservative
- configuration uses one primary YAML file with optional environment overrides

## Non-Goals for This Task List

This MVP task list does not include:

- CIDR fallback
- proxy-aware forwarded-header handling
- RAG orchestration
- billing exports
- model deployment control APIs
- generalized multi-service gateway behavior

## Implementation Sequence

The recommended order is:

1. project skeleton
2. configuration system
3. routing layer
4. model discovery endpoint
5. chat completions proxy
6. responses proxy
7. source resolution
8. metrics and token accounting
9. streaming pass-through
10. end-to-end validation

This order keeps the critical path simple: build a working gateway first, then add attribution, metrics, and streaming complexity in controlled steps.

## Task Breakdown

### 1. Project Skeleton

Goal:

- establish the service foundation

Tasks:

- choose the implementation language and HTTP framework
- create the initial project layout
- add the main application entrypoint
- add `GET /healthz`
- add `GET /metrics`
- add basic structured logging

Done when:

- the service starts locally
- `GET /healthz` returns success
- `GET /metrics` exposes a valid Prometheus endpoint

### 2. Configuration System

Goal:

- make runtime behavior explicit and configuration-driven

Tasks:

- define the YAML config schema
- support `server`
- support `timeouts`
- support `routing`
- support `upstreams`
- support `departments`
- validate config at startup
- add `config.example.yaml`

Done when:

- valid config loads successfully
- invalid config fails fast with readable errors
- multiple upstreams and department mappings can be declared without code changes

### 3. Routing Layer

Goal:

- route requests to the correct upstream pool for the requested model and provide one source of truth for model availability

Tasks:

- build a `model_name -> upstream pool` view from config
- track healthy upstreams
- implement round-robin selection within one model pool
- handle "no healthy upstream available" failures
- add route decision logging

Done when:

- requests for one model rotate across healthy upstreams serving that model
- unhealthy upstreams are skipped
- routing failures are explicit and debuggable

### 4. Model Discovery Endpoint

Goal:

- let users discover which models are currently available through the gateway

Tasks:

- implement `GET /v1/models`
- return models from the routing registry
- derive model availability from configured upstreams and health state
- keep the response read-only and user-facing
- avoid exposing internal machine IPs or instance addresses

Done when:

- a user can query `GET /v1/models`
- the response reflects the current routing registry
- unavailable models are handled consistently according to the chosen response semantics

### 5. Chat Completions Proxy

Goal:

- provide a functioning non-streaming request path

Tasks:

- implement `POST /v1/chat/completions`
- validate the request shape needed by the gateway
- extract the requested `model_name`
- forward the request to the selected vLLM upstream
- return the upstream response transparently
- handle upstream timeout and connection failure paths

Done when:

- a client can submit a non-streaming chat completion through the gateway
- upstream success and failure paths are visible and consistent

### 6. Responses Proxy

Goal:

- provide a functioning `/v1/responses` path for code agents and responses-based clients

Tasks:

- implement `POST /v1/responses`
- validate the request shape needed by the gateway
- extract the requested `model_name`
- forward the request to the selected vLLM upstream
- return the upstream response transparently
- handle upstream timeout and connection failure paths

Done when:

- a client can submit a responses request through the gateway
- code-agent-style clients do not need a separate compatibility endpoint
- upstream success and failure paths are visible and consistent

### 7. Source Resolution

Goal:

- resolve each request into a bounded `department` identity

Tasks:

- extract API key from the request
- map API key to `department`
- apply `department="unknown"` fallback
- emit `gateway_source_resolution_total`
- define handling for missing or unmapped API keys
- add tests for source resolution behavior

Done when:

- mapped API keys resolve to the expected department
- unmapped requests are visible as `unknown`
- department labels remain bounded and stable

### 8. Metrics and Token Accounting

Goal:

- satisfy the raw metric contract for `vllm-usage-observability`

Tasks:

- emit `gateway_http_requests_total`
- emit `gateway_request_duration_seconds_bucket`
- emit `gateway_prompt_tokens_total`
- emit `gateway_generation_tokens_total`
- emit `gateway_token_accounting_total`
- define `recorded`, `missing_usage`, and `parse_error`
- ensure timeout, cancel, and upstream error paths do not record token counters by default

Done when:

- metric names and labels match the contract in `README.md`
- request metrics are emitted for handled traffic
- token metrics are emitted only for reliable completed usage
- missing accounting is visible rather than silently guessed

### 9. Streaming Pass-Through

Goal:

- support streaming responses without turning the gateway into a buffering layer

Tasks:

- support streaming for `/v1/chat/completions`
- support streaming for `/v1/responses` where applicable
- keep the client connection open while upstream data arrives
- forward upstream chunks/events as they are received
- stop upstream work when the client disconnects if possible
- finalize metrics when the stream completes or fails clearly

Done when:

- a streaming chat completion works through the gateway
- a streaming responses request works through the gateway
- the gateway does not need to fully buffer the response before forwarding
- streaming success and failure paths remain observable

### 10. End-to-End Validation

Goal:

- verify that the gateway behaves correctly as a thin source-aware entry layer

Tasks:

- run the gateway against one or more vLLM upstreams
- verify `GET /v1/models`
- verify `POST /v1/responses`
- verify API-key-based department resolution
- verify model-aware round-robin routing
- verify required metrics appear on `/metrics`
- verify token accounting behavior for success and missing-usage cases
- verify streaming pass-through behavior

Done when:

- the gateway can serve real requests end-to-end
- emitted metrics match the README contract
- validation results are explainable and repeatable

## Recommended First Issue List

The first implementation batch should open these issues:

1. Bootstrap service with `/healthz` and `/metrics`
2. Define YAML config schema and startup validation
3. Implement model-aware upstream registry and round-robin routing
4. Implement read-only `GET /v1/models`
5. Implement non-streaming `POST /v1/chat/completions` proxy
6. Implement `POST /v1/responses` proxy for code-agent compatibility
7. Implement API-key-to-department resolution
8. Implement Prometheus metrics per contract
9. Implement conservative token accounting
10. Add streaming pass-through for chat completions and responses
11. Add end-to-end validation checklist

## Risks and Trade-Offs

- implementing streaming too early can slow down the whole MVP
- mixing routing and source resolution logic can blur responsibilities
- trying to "fill in" missing token usage will undermine metric trustworthiness
- adding future platform concerns too early will erode the thin-gateway boundary

## Validation Approach

Each milestone should be validated with the smallest useful check:

- skeleton: service starts and exposes health/metrics endpoints
- config: invalid config fails fast
- model discovery: `GET /v1/models` reflects the routing registry
- proxy: a non-streaming request succeeds end-to-end
- responses: a `/v1/responses` request succeeds end-to-end
- source resolution: API keys map to departments predictably
- routing: requests rotate across healthy upstreams for one model
- metrics: labels remain bounded and match the contract
- token accounting: missing usage does not produce guessed token counters
- streaming: clients receive streamed data progressively through the gateway

## Final Rule

Default behavior:

> Build the smallest useful gateway first, then add attribution and observability carefully without expanding the scope into a full platform.
