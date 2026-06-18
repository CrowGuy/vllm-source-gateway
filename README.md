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
- exposing a read-only model discovery endpoint
- transparently forwarding normal and streaming responses from vLLM
- resolving request source into `department`
- proxying the request to a selected vLLM upstream
- recording request and token metrics using bounded labels
- exposing `/metrics` for Prometheus scraping

## MVP Design Decisions

The current MVP intentionally keeps scope narrow so the gateway can be built, validated, and integrated with `vllm-usage-observability` before taking on broader platform concerns.

### API Surface

The first version supports:

- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/responses`

### Model Discovery

The gateway should provide a read-only model discovery endpoint so users can check which models are currently available without relying on manual email notifications.

For MVP:

- expose `GET /v1/models`
- return models known to the gateway routing registry
- derive model availability from configured upstreams and health state
- keep the response user-facing and avoid exposing internal machine IPs

Deferred:

- deployment control through the gateway
- model lifecycle management workflows
- administrative write APIs for model registration

### Responses API

MVP should support `/v1/responses` so code agents and responses-based clients can use the gateway without requiring a separate compatibility path.

Current scope:

- the gateway guarantees `POST /v1/responses` proxy compatibility for stateless request forwarding
- the gateway does not currently guarantee full stateful Responses API semantics such as stored-response retrieval, `previous_response_id`, or `GET /v1/responses/{id}`

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

The same routing registry is also the source of truth for `GET /v1/models`.

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
- default behavior is to allow unknown or missing keys and attribute them to `department="unknown"`
- deployments that use the gateway as an ingress auth boundary may enable `security.reject_unknown_api_keys=true`

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
- token metrics are recorded only for upstream `2xx` responses with reliable usage

## Proxy Header Policy

Header forwarding should stay conservative.

For the current gateway:

- request forwarding keeps a bounded allowlist of useful client headers
- `authorization`, `x-api-key`, `cookie`, `accept-encoding`, and hop-by-hop headers are not forwarded upstream
- when an upstream declares `authorization_from_env`, the gateway injects its own `Authorization: Bearer <token>` header for upstream traffic and health probes
- downstream hop-by-hop headers are stripped before returning the response
- `content-encoding` is also stripped from downstream proxy responses to avoid mismatches after client-side decoding

## Configuration Model

The preferred MVP configuration model is one primary YAML file, with environment variables reserved for deployment-specific overrides.

Configuration areas should include:

- `server`
- `timeouts`
- `health`
- `routing`
- `security`
- `upstreams`
- `departments`

Current baseline:

- `server.max_request_body_bytes` limits JSON proxy request bodies before parsing
- requests larger than the configured limit are rejected with `413`
- departments may use inline `api_keys` for local development or `api_keys_from_env` for production secret handling
- each department must declare exactly one of `api_keys` or `api_keys_from_env`
- upstreams may declare `authorization_from_env` for per-upstream bearer auth injection
- env-backed upstream auth values must be raw token strings without the `Bearer ` prefix
- configuration changes currently require a gateway restart to take effect; hot reload is not part of the current contract

Deferred:

- trusted proxy configuration
- CIDR fallback configuration

## Local Development

Recommended local development uses `uv` with Python 3.11 or newer.

### Prerequisites

- Python 3.11+
- `uv`

### Environment Setup

Create and activate a virtual environment:

```bash
uv venv
source .venv/bin/activate
```

Install runtime dependencies:

```bash
uv sync
```

Install development dependencies when you want pytest or lint tooling:

```bash
uv sync --extra dev
```

### Start the Gateway

Set the config path:

```bash
export VLLM_SOURCE_GATEWAY_CONFIG=./config.example.yaml
```

Start the gateway:

```bash
python -m uvicorn vllm_source_gateway.main:app --host 0.0.0.0 --port 8080
```

### Container Build

Build the image:

```bash
docker build -t vllm-source-gateway:local .
```

Run the container with a mounted config file and env-backed secrets:

```bash
docker run --rm \
  -p 8080:8080 \
  -v "$(pwd)/config.yaml:/app/config.yaml:ro" \
  -e VLLM_SOURCE_GATEWAY_CONFIG=/app/config.yaml \
  -e DEPT_FINANCE_API_KEYS=finance-prod \
  -e UPSTREAM_GPT_OSS_120B_A_TOKEN=replace-me \
  vllm-source-gateway:local
```

Container notes:

- the image expects configuration to be provided at runtime rather than baked into the image
- configuration changes still require a container restart to take effect
- production secrets should be injected through environment variables referenced by `api_keys_from_env`
- upstream bearer tokens should also be injected through environment variables referenced by `authorization_from_env`

## Production With Docker Compose

The current production recommendation is:

- use `config.prod.yaml` for the deployed routing and secret-name contract
- use `docker-compose.prod.yml` to run the container
- use a single `.env.prod` file to hold real department API keys and upstream bearer tokens
- treat the Docker image as an environment-agnostic artifact
- keep environment-specific values in `config.prod.yaml` and `.env.prod`, not in the image

Tracked sample files:

- `config.prod.yaml`
- `docker-compose.prod.yml`
- `.env.prod.example`

Runtime secret rules:

- `config.prod.yaml` stores env var names only
- `.env.prod` stores the real values
- `.env.prod` must not be committed
- `.env.prod` should be maintained by the deployment owner on the target host
- changing `config.prod.yaml` or `.env.prod` still requires a container restart
- `/metrics` should be treated as an internal observability surface, not a public endpoint

### Production Setup

Create the runtime env file from the tracked example:

```bash
cp .env.prod.example .env.prod
```

Edit `.env.prod` with real values. Department keys and upstream tokens can live in the same file:

```env
DEPT_FINANCE_API_KEYS=finance-key-1,finance-key-2
DEPT_HR_API_KEYS=hr-key-1
DEPT_DATA_PLATFORM_API_KEYS=data-platform-key-1

UPSTREAM_GEMMA4_A_TOKEN=replace-me
UPSTREAM_GEMMA4_B_TOKEN=replace-me
UPSTREAM_QWEN3_32B_A_TOKEN=replace-me
```

Bring the gateway up with Docker Compose:

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Stop it:

```bash
docker compose -f docker-compose.prod.yml down
```

### Offline Image Transport

When the target environment cannot build or pull images directly, move a tested image into the
offline environment instead of rebuilding there.

Build and tag the image on a connected machine:

```bash
docker compose -f docker-compose.prod.yml build
docker tag vllm-source-gateway:prod vllm-source-gateway:prod-offline
```

Export the image:

```bash
docker save -o vllm-source-gateway-prod-offline.tar vllm-source-gateway:prod-offline
```

On the offline host, load the image and start the service:

```bash
docker load -i vllm-source-gateway-prod-offline.tar
docker compose -f docker-compose.prod.yml up -d
```

Recommended deployment bundle for an offline host:

- `docker-compose.prod.yml`
- `config.prod.yaml`
- `.env.prod`
- `vllm-source-gateway-prod-offline.tar`

### Naming Convention

Recommended env var naming:

- department API keys: `DEPT_<DEPARTMENT_NAME>_API_KEYS`
- upstream bearer tokens: `UPSTREAM_<MODEL_OR_SERVICE>_<INSTANCE>_TOKEN`

Examples:

- `DEPT_FINANCE_API_KEYS`
- `DEPT_DATA_PLATFORM_API_KEYS`
- `UPSTREAM_GEMMA4_A_TOKEN`
- `UPSTREAM_QWEN3_32B_A_TOKEN`

Operational guidance:

- upstream tokens are managed per upstream service instance
- upstream token values must contain the token body only, without the `Bearer ` prefix
- if an env-backed department key or upstream token is missing, gateway startup fails fast

### Production Host Baseline

This gateway has been validated successfully on a newer production host and has previously failed on
an older host with container `exit=139` segmentation faults.

Operational recommendation:

- use a modern Linux host with a current Docker Engine / Compose runtime
- do not treat very old Docker Compose or host OS versions as a supported baseline
- if possible, validate the exact image tag on the target host before declaring it production-ready
- expose `/metrics` only to internal Prometheus or trusted internal network paths

If the deployment environment is constrained, prefer moving a tested image to a newer host over
rebuilding the gateway inside an older runtime stack.

### Basic Smoke Checks

Liveness:

```bash
curl http://127.0.0.1:8080/livez
```

Readiness:

```bash
curl http://127.0.0.1:8080/readyz
```

Metrics:

```bash
curl http://127.0.0.1:8080/metrics
```

Smoke validation after a Compose deploy:

```bash
curl http://127.0.0.1:8080/livez
curl http://127.0.0.1:8080/readyz
curl http://127.0.0.1:8080/v1/models
```

### Post-Deploy Validation Checklist

After `docker compose up -d`, validate in this order:

1. container status

```bash
docker compose -f docker-compose.prod.yml ps
```

2. recent gateway logs

```bash
docker compose -f docker-compose.prod.yml logs gateway --tail=200
```

3. liveness and readiness

```bash
curl http://127.0.0.1:8080/livez
curl http://127.0.0.1:8080/readyz
```

4. model discovery

```bash
curl -sS http://127.0.0.1:8080/v1/models
```

5. non-streaming request path

```bash
curl -sS \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -X POST "http://127.0.0.1:8080/v1/chat/completions" \
  --data '{
    "model": "gemma-4-31b",
    "messages": [{"role": "user", "content": "Reply with exactly: hello from gateway"}]
  }'
```

6. streaming request path

```bash
curl -sS -N \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -X POST "http://127.0.0.1:8080/v1/chat/completions" \
  --data '{
    "model": "gemma-4-31b",
    "messages": [{"role": "user", "content": "Count to 3 in one short line"}],
    "stream": true
  }'
```

7. metrics contract visibility

```bash
curl -sS http://127.0.0.1:8080/metrics | grep gateway_http_requests_total
curl -sS http://127.0.0.1:8080/metrics | grep gateway_source_resolution_total
curl -sS http://127.0.0.1:8080/metrics | grep gateway_token_accounting_total
```

If the upstream returns reliable usage, also check:

```bash
curl -sS http://127.0.0.1:8080/metrics | grep gateway_prompt_tokens_total
curl -sS http://127.0.0.1:8080/metrics | grep gateway_generation_tokens_total
```

### Troubleshooting

Common deployment failures and first checks:

- container exits immediately with missing env errors
  - verify `.env.prod` contains every `DEPT_*_API_KEYS` and `UPSTREAM_*_TOKEN` referenced by `config.prod.yaml`
- `/readyz` fails while `/livez` succeeds
  - verify upstream `base_url` reachability and upstream bearer token correctness
- `/v1/models` is empty or models are `unavailable`
  - verify upstream health probe path and upstream authentication
- requests return `401`
  - verify the caller is using a mapped API key or check whether `security.reject_unknown_api_keys=true`
- container restarts repeatedly
  - inspect `docker compose logs gateway`
  - inspect `docker ps -a` and `docker inspect <container_id>`
- older hosts show container `exit=139`
  - treat this as a runtime compatibility issue first
  - move the tested image to a newer host rather than debugging application logic first

For one real-upstream validation flow using `gemma-4-31b`, see
[docs/e2e-validation-gemma4.md](/home/randy/Documents/crow/vllm-source-gateway/docs/e2e-validation-gemma4.md).

`/healthz` remains available as a backward-compatible alias for liveness.

## Interop Contract with `vllm-usage-observability`

This repo must expose raw metrics that the observability repo can consume as stable inputs.

Security posture for `/metrics`:

- `/metrics` is intended for internal Prometheus scrape traffic
- do not treat unauthenticated public exposure of `/metrics` as an accepted production posture
- if stronger protection is needed, enforce it at the deployment or network boundary

Observability scope:

- the current bounded metrics contract is optimized for source-attributed proxy traffic
- `/v1/chat/completions` and `/v1/responses` are the primary routes expected to feed department-level request, token, and failure views
- `/v1/models`, `/livez`, `/readyz`, `/healthz`, `/metrics`, generic `404` responses, and some pre-proxy failures are not currently first-class parts of the source-attributed metrics contract
- access logging follows the same bias toward operationally relevant proxy traffic

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
- use LLM-oriented buckets spanning `0.1s` through `300s`

#### `gateway_http_request_failures_total`

Labels emitted by this repo:

- `department`
- `endpoint`
- `method`
- `status_class`
- `failure_origin`

Semantic rules:

- increment only for request outcomes with HTTP `4xx` or `5xx`
- `failure_origin="gateway"` means the gateway synthesized the failure response
- `failure_origin="upstream"` means the upstream returned a non-2xx response that the gateway passed through
- keep `failure_origin` bounded to `gateway` or `upstream`

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
security:
  reject_unknown_api_keys: false

departments:
  finance:
    api_keys_from_env: DEPT_FINANCE_API_KEYS

  hr:
    api_keys_from_env: DEPT_HR_API_KEYS

  data_platform:
    api_keys_from_env: DEPT_DATA_PLATFORM_API_KEYS
```

Environment value format:

- comma-separated string, for example `DEPT_FINANCE_API_KEYS=finance-prod,finance-batch`
- or JSON array string, for example `DEPT_FINANCE_API_KEYS=["finance-prod","finance-batch"]`

Upstream auth format:

- `authorization_from_env` points to an env var whose value is the token body only
- do not include the `Bearer ` prefix in the env var value
- missing or empty upstream auth env values fail startup

Operational rules:

- production deployments should prefer `api_keys_from_env` over inline secrets
- production deployments should also prefer `authorization_from_env` over inline upstream credentials
- missing or empty env-backed secrets fail startup
- secret values are not logged

## Deployment Notes

The gateway is an I/O-heavy service, not a GPU inference service.

Logs are expected to be structured JSON lines.

Operationally useful baseline fields include:

- `message`
- `logger`
- `level`
- `method`
- `path`
- `endpoint`
- `department`
- `status_code`
- `duration_seconds`
- `request_id` when present
- `trace_id` when present

For MVP planning:

- GPU is not required
- prioritize stable networking, moderate CPU, and enough memory for concurrent connections
- current deployment baseline is single process / single worker per container
- current in-memory routing, health, and metrics state are intentionally accepted under that baseline
- treat multi-worker or horizontally scaled shared-state support as future architecture work, not a current guarantee

Representative starting point:

- `4-8 vCPU`
- `8-16 GB RAM`

Final sizing depends more on concurrency, streaming duration, and network throughput than on model compute.

## MVP Scope

The MVP should stay intentionally small.

### In Scope

- one gateway service
- read-only `GET /v1/models`
- support for `/v1/chat/completions`
- support for `/v1/responses`
- static upstream list for multiple vLLM services
- streaming pass-through
- source resolution from API key
- department-level Prometheus metrics
- basic timeout handling
- liveness and readiness endpoints
- configuration-driven department mapping

### Out of Scope

- streaming-specific advanced accounting beyond what is needed for correct request completion metrics
- dynamic service discovery
- tenant admin UI
- quota management
- billing exports
- RAG orchestration
- database access
- model deployment control APIs
- CIDR fallback-based source resolution
- generalized multi-service gateway behavior

## MVP Success Criteria

The MVP is successful when:

1. users can query `GET /v1/models` to discover which models are currently available
2. callers use the gateway instead of talking directly to vLLM
3. code agents and responses-based clients can call the gateway through `/v1/responses`
4. one department using multiple IPs still appears as one department in Prometheus metrics
5. the observability repo can derive department-level request, token, error-rate, and latency views from the emitted metrics
6. `department="unknown"` is visible and explainable

One successful real-upstream validation record is documented in
[docs/e2e-validation-gemma4.md](/home/randy/Documents/crow/vllm-source-gateway/docs/e2e-validation-gemma4.md).

## Operational Risks

- the gateway becomes a new failure point if timeouts and health handling are weak
- API-key ownership can drift if keys are shared or poorly governed
- token accounting can become incomplete if upstream usage is missing or parsed inconsistently

Current reviewer-feedback triage and hardening priorities are documented in
[docs/reviewer-findings-triage.md](/home/randy/Documents/crow/vllm-source-gateway/docs/reviewer-findings-triage.md).

## Recommended Evolution Path

After the MVP is stable, future phases may add:

- stronger auth integration
- richer routing and failover behavior
- limited rate limiting
- event/log-based attribution for high-cardinality or audit-heavy use cases

Those phases should be added only after the thin gateway boundary is working and observed in production-like environments.
