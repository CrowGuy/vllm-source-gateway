# vllm-source-gateway
Thin gateway in front of vLLM for source resolution, department-level attribution, request forwarding, and Prometheus-safe metrics.

## Purpose

This repository provides a thin access layer in front of one or more vLLM services.

Its job is not to replace vLLM. Its job is to handle entry-layer concerns that do not belong inside vLLM itself:

- request ingress
- source resolution
- department mapping
- forwarding to vLLM
- source-aware Prometheus metrics

## Why This Repository Exists

The current deployment exposes multiple vLLM services directly to callers. That is sufficient for basic inference access, but it leaves an important gap:

- the system cannot safely observe usage by caller department
- one department may use multiple IPs
- raw IP is not an acceptable Prometheus label for long-term observability

This gateway closes that gap by resolving request origin into a bounded `department` identity before requests are proxied upstream.

## Non-Goals

This repository is not the first version of a full AI platform gateway.

It does not initially aim to provide:

- RAG orchestration
- database APIs
- prompt workflow management
- billing truth
- generalized routing for every backend service
- per-IP analytics as a Prometheus label space

## Core Responsibilities

The first version of the gateway is responsible for:

- receiving supported LLM API requests
- transparently forwarding normal and streaming responses from vLLM
- resolving request source into `department`
- proxying the request to a selected vLLM upstream
- recording request and token metrics using bounded labels
- exposing `/metrics` for Prometheus scraping

## MVP Design Decisions

The current MVP intentionally keeps scope narrow so the gateway can be built, validated, and integrated with `vllm-usage-observability` before taking on broader platform concerns.

### API Surface

The first version supports:

- `POST /v1/chat/completions`

Deferred:

- `POST /v1/responses`

### Streaming Behavior

The gateway must support streaming pass-through for chat completions.

For MVP, this means:

- keep the client connection open while upstream vLLM streams data
- forward upstream chunks/events to the client without unnecessary response rewriting
- finalize request metrics only when the stream completes or fails clearly

The gateway is part of the response path. It does not generate model output, but it does proxy both non-streaming and streaming responses from vLLM.

### Upstream Routing Policy

Routing is model-aware.

For MVP:

1. resolve the requested `model_name`
2. select the upstream pool serving that model
3. use round-robin across healthy upstreams in that pool

Deferred:

- weighted routing
- least-loaded routing
- latency-aware routing
- sticky routing

## Request Flow

```text
Client
  -> Gateway
  -> source resolution
  -> department assignment
  -> forward request to vLLM
  -> collect response usage
  -> emit metrics
  -> return response
```

## Source Resolution Rules

Resolution should prefer stable identity over network heuristics.

Recommended precedence:

1. API key to department
2. fallback to `department="unknown"`

Design rules:

- API key is the primary source identity for MVP
- department names must be bounded, stable, and configuration-driven
- raw IP is not part of the metrics contract

Deferred design:

- auth-subject-based resolution
- trusted CIDR fallback for environments with stable internal network rules
- proxy-aware `X-Forwarded-For` handling when a trusted proxy layer exists

## Token Accounting Semantics

Token accounting must be conservative.

For MVP:

- record token counters only when usage is known reliably for a completed request
- do not estimate missing usage
- do not reconstruct token counts from aggregate vLLM metrics

When token usage is unavailable:

- request metrics are still recorded
- token metrics are not incremented
- validation metrics should reflect missing accounting coverage

For failed or incomplete requests:

- request error metrics should be recorded
- token metrics should not be recorded by default for timeout, cancellation, or upstream error paths unless reliable usage semantics are introduced later

## Configuration Model

The preferred MVP configuration model is one primary YAML file, with environment variables reserved for deployment-specific overrides.

Configuration areas should include:

- `server`
- `timeouts`
- `routing`
- `upstreams`
- `departments`

Deferred:

- trusted proxy configuration
- CIDR fallback configuration

## Interop Contract with `vllm-usage-observability`

This repo must expose raw metrics that the observability repo can consume as stable inputs.

### Required Metrics

#### `gateway_http_requests_total`

Labels emitted by this repo:

- `department`
- `endpoint`
- `method`
- `status_class`

Semantic rules:

- increment once per handled request
- `status_class` must be `2xx`, `4xx`, or `5xx`
- `endpoint` must be a bounded logical name such as `chat_completions`

#### `gateway_request_duration_seconds_bucket`

Labels emitted by this repo:

- `department`
- `endpoint`
- `method`
- `le`

Semantic rules:

- measure end-to-end gateway handling time including upstream proxying

#### `gateway_prompt_tokens_total`

Labels emitted by this repo:

- `department`
- `model_name`

Semantic rules:

- increment only when prompt token usage is known for the completed request

#### `gateway_generation_tokens_total`

Labels emitted by this repo:

- `department`
- `model_name`

Semantic rules:

- increment only when generation token usage is known for the completed request

### Recommended Validation Metrics

#### `gateway_source_resolution_total`

Labels:

- `department`
- `resolution_source`

#### `gateway_token_accounting_total`

Labels:

- `endpoint`
- `accounting_status`

### Deployment Labels

These labels may be attached by Prometheus scrape configuration instead of the application itself:

- `env`
- `region`
- `instance_name`

### Forbidden Labels

Do not expose these as Prometheus labels for the contract consumed by the observability repo:

- `client_ip`
- `x_forwarded_for`
- `request_id`
- `api_key_id`
- `user_id`

## Example Department Mapping

```yaml
departments:
  finance:
    api_keys:
      - finance-prod

  hr:
    api_keys:
      - hr-app

  data_platform:
    api_keys:
      - dp-prod
      - dp-batch
```

## Deployment Notes

The gateway is an I/O-heavy service, not a GPU inference service.

For MVP planning:

- GPU is not required
- prioritize stable networking, moderate CPU, and enough memory for concurrent connections
- design the service to remain stateless so it can scale horizontally later

Representative starting point:

- `4-8 vCPU`
- `8-16 GB RAM`

Final sizing depends more on concurrency, streaming duration, and network throughput than on model compute.

## MVP Scope

The MVP should stay intentionally small.

### In Scope

- one gateway service
- support for `/v1/chat/completions`
- static upstream list for multiple vLLM services
- streaming pass-through
- source resolution from API key
- department-level Prometheus metrics
- basic timeout handling
- health endpoint
- configuration-driven department mapping

### Out of Scope

- streaming-specific advanced accounting beyond what is needed for correct request completion metrics
- dynamic service discovery
- tenant admin UI
- quota management
- billing exports
- RAG orchestration
- database access
- `/v1/responses`
- CIDR fallback-based source resolution
- generalized multi-service gateway behavior

## MVP Success Criteria

The MVP is successful when:

1. callers use the gateway instead of talking directly to vLLM
2. one department using multiple IPs still appears as one department in Prometheus metrics
3. the observability repo can derive department-level request, token, error-rate, and latency views from the emitted metrics
4. `department="unknown"` is visible and explainable

## Operational Risks

- the gateway becomes a new failure point if timeouts and health handling are weak
- API-key ownership can drift if keys are shared or poorly governed
- token accounting can become incomplete if upstream usage is missing or parsed inconsistently

## Recommended Evolution Path

After the MVP is stable, future phases may add:

- stronger auth integration
- richer routing and failover behavior
- limited rate limiting
- event/log-based attribution for high-cardinality or audit-heavy use cases

Those phases should be added only after the thin gateway boundary is working and observed in production-like environments.
