# vllm-source-gateway
Thin gateway in front of vLLM for source resolution, department-level attribution, request forwarding, and Prometheus-safe metrics.

## Purpose

This repository provides a thin access layer in front of one or more vLLM services.

Current launch status:

> Launch-ready for the current single-process, current-model-mix production baseline.
> Same-model multi-upstream round-robin behavior has been validated in production.
> Same-model connect-stage failover behavior has been validated in production.

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
- `POST /v1/messages`

Compatibility freeze:

- the current launch cycle intentionally freezes supported LLM proxy paths at `POST /v1/chat/completions`, `POST /v1/responses`, and `POST /v1/messages`
- new API surfaces should be treated as post-launch work and require a separate product and compatibility decision
- launch-stability work should prioritize reliability, rollback safety, observability correctness, and operator runbooks before adding another proxy path

### Support Matrix

This matrix summarizes the current supported caller paths and the validation confidence behind them.

| Caller path | Proxy mode | Non-streaming | Streaming | Tool use | Token accounting | Production validation |
| --- | --- | --- | --- | --- | --- | --- |
| `POST /v1/chat/completions` | Native upstream proxy | Supported | Supported | Pass-through by upstream behavior | Conservative usage extraction when upstream returns reliable usage | Real-upstream E2E validation completed for non-streaming and streaming |
| `POST /v1/responses` | Native stateless upstream proxy | Supported | Supported | Validated for active real-caller scenarios | Conservative usage extraction with `recorded` or `missing_usage` status | Production validation completed for non-streaming, streaming, and tool-use parity |
| `POST /v1/messages` | Native upstream `messages` proxy | Supported | Supported | Validated for production-like tool-use edge cases | Streaming token accounting validated against real upstream behavior | Production validation completed for native proxy behavior, streaming accounting, and tool use |

Support boundaries:

- `GET /v1/models` is supported for read-only model discovery, not inference.
- `POST /v1/responses` is scoped to stateless proxy behavior and does not guarantee stored-response lifecycle semantics.
- `POST /v1/messages` follows upstream vLLM `messages` behavior and does not guarantee broader Anthropic lifecycle APIs.
- Tool-use semantics are delegated to the upstream model and vLLM implementation; the gateway validates routing needs and preserves pass-through behavior.
- Token counters are emitted only when usage is reliable; missing usage is reported through token-accounting status instead of being estimated.

Rerun the relevant validation checklist after:

- upgrading vLLM
- changing model deployments
- adding or removing upstreams
- changing upstream auth or department API keys
- changing the main caller population or tool-use request shapes

### Model Discovery

The gateway provides two read-only model discovery surfaces:

- `GET /v1/models` is the machine-readable compatibility API for clients, SDKs, agents, and OpenAI-aware tools.
- `GET /models` is a human-readable model catalog for trusted internal users.

`GET /models` has been deployed and validated in production for the current
single-process, current-model-mix baseline. It is an internal service catalog UI,
not a new compatibility API path and not an inference endpoint.

For machine-readable discovery:

- expose `GET /v1/models`
- return models known to the gateway routing registry
- derive model availability from configured upstreams and health state
- keep the response user-facing and avoid exposing internal machine IPs

For human-readable discovery:

- expose `GET /models`
- render a simple internal catalog page for users who need to decide which model to call
- combine static catalog metadata from `model_catalog` with dynamic availability from routing health state
- require each `model_catalog` key to exactly match a gateway-facing model id from `GET /v1/models`
- keep deployment location descriptions abstract, such as `DGX-A100`, `RTX-5090-PC-1`, or `Lab GPU Server`
- do not expose upstream IPs, upstream URLs, hostnames, bearer tokens, or other topology-sensitive details
- list only gateway-supported API paths in `api_paths`; currently supported values are `chat`, `responses`, and `messages`

Catalog status semantics:

- `online`: all configured upstreams for that model are currently healthy
- `degraded`: at least one upstream is healthy and at least one upstream is unhealthy
- `offline`: zero upstreams for that model are currently healthy

Deferred:

- deployment control through the gateway
- model lifecycle management workflows
- administrative write APIs for model registration

### Responses API

MVP should support `/v1/responses` so code agents and responses-based clients can use the gateway without requiring a separate compatibility path.

Current scope:

- the gateway guarantees `POST /v1/responses` proxy compatibility for stateless request forwarding
- the gateway does not currently guarantee full stateful Responses API semantics such as stored-response retrieval, `previous_response_id`, or `GET /v1/responses/{id}`

Current validation and behavior:

- `/v1/responses` is treated as a native stateless upstream proxy path
- production validation has already confirmed:
  - non-streaming and streaming `/v1/responses` behavior works against a real upstream
  - tool-use parity for active real-caller scenarios works through the current proxy path
- the current compatibility surface is intentionally frozen at `chat/completions`, `responses`, and `messages` while the project finishes launch-stability and operational hardening work

### Anthropic Messages API

The gateway also supports a native proxy path for `POST /v1/messages` so Anthropic-oriented callers can reach the same vLLM-backed model pool without a separate ingress.

Current scope:

- the gateway guarantees `POST /v1/messages` only
- the gateway treats `/v1/messages` as a stateless proxy scope only
- the gateway proxies requests to upstream `POST /v1/messages` without gateway-side Anthropic-to-OpenAI translation
- the gateway keeps gateway-level concerns such as source resolution, routing, auth injection, failover, metrics, and conservative token accounting
- request and response semantics for tools, thinking, multimodal blocks, and other Anthropic-native fields are determined by the upstream vLLM `messages` implementation
- the gateway does not currently guarantee full Anthropic stateful semantics such as stored-message retrieval or broader lifecycle APIs beyond `POST /v1/messages`

Current validation and behavior:

- gateway-side request validation stays minimal:
  - request body must be a JSON object
  - `model` must be present so the gateway can route the request
- beyond that minimal validation, request fields are passed through to upstream `/v1/messages`
- upstream non-2xx `messages` responses are passed through raw when a response exists
- unit and endpoint tests now cover native `/v1/messages` proxy behavior, header policy, usage accounting, and streaming pass-through
- initial production validation has already confirmed:
  - native `/v1/messages` streaming token accounting can reach `accounting_status="recorded"` with a real upstream
  - production-like tool-use requests can complete successfully through the native `/v1/messages` path
- the current compatibility surface is intentionally frozen at `chat/completions`, `responses`, and `messages` while the project finishes launch-stability and operational hardening work

Stateful semantics explicitly out of scope:

- stored message retrieval or replay semantics
- broader Anthropic lifecycle or state management features
- any guarantee of full wire-level compatibility with every Anthropic SDK feature beyond upstream `POST /v1/messages`

### Streaming Behavior

The gateway supports streaming pass-through for the frozen proxy paths:

- `POST /v1/chat/completions`
- `POST /v1/responses`
- `POST /v1/messages`

For MVP, this means:

- keep the client connection open while upstream vLLM streams data
- forward upstream chunks/events to the client without unnecessary response rewriting
- keep SSE usage parsing bounded so malformed upstream streams cannot grow decode buffers without limit
- finalize request metrics only when the stream completes or fails clearly

The gateway is part of the response path. It does not generate model output, but it does proxy both non-streaming and streaming responses from vLLM.

### Upstream Routing Policy

Routing is model-aware.

For MVP:

1. resolve the requested `model_name`
2. select the upstream pool serving that model
3. use round-robin across healthy upstreams in that pool
4. if a selected upstream fails during a connect-stage request attempt, retry another healthy upstream for the same model before failing the request

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
- `server.max_sse_decode_buffer_bytes` bounds in-memory SSE usage parsing state before the gateway disables further stream-usage parsing for that response
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
- changing `config.prod.yaml` or `.env.prod` requires recreating the container, not only restarting it
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

When `.env.prod` changes, recreate the container so Compose injects the new environment values:

```bash
docker compose -f docker-compose.prod.yml up -d --force-recreate
```

A plain container restart is not enough for `.env.prod` changes. Docker reads `env_file` values when
the container is created; existing containers keep their original environment.

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
- the production container now runs as a dedicated non-root application user
- the production Compose definition now includes a container-level healthcheck against `/livez`
- keep `/readyz` as the stronger service-readiness signal for upstream availability checks
- mounted config files such as `config.prod.yaml` should remain readable by that runtime user
- expose `/metrics` only to internal Prometheus or trusted internal network paths

If the deployment environment is constrained, prefer moving a tested image to a newer host over
rebuilding the gateway inside an older runtime stack.

### Release Gate

Use this gate before promoting a new image, `config.prod.yaml`, or `.env.prod` change.

Required checks:

1. local tests pass

```bash
pytest
```

2. production image builds or a previously tested image is available

```bash
docker compose -f docker-compose.prod.yml build
docker images | grep vllm-source-gateway
```

3. runtime configuration and secrets are present

```bash
test -f config.prod.yaml
test -f .env.prod
grep -E '^(DEPT_.*_API_KEYS|UPSTREAM_.*_TOKEN)=' .env.prod | cut -d= -f1
```

4. container starts and becomes healthy

```bash
docker compose -f docker-compose.prod.yml up -d --force-recreate
docker compose -f docker-compose.prod.yml ps
docker inspect --format '{{json .State.Health}}' vllm-source-gateway
```

5. liveness, readiness, and model discovery pass

```bash
curl -fsS http://127.0.0.1:8080/livez
curl -fsS http://127.0.0.1:8080/readyz
curl -fsS http://127.0.0.1:8080/v1/models
```

6. frozen compatibility paths pass smoke validation

```bash
curl -fsS \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -X POST "http://127.0.0.1:8080/v1/chat/completions" \
  --data '{"model":"'"${MODEL_NAME}"'","messages":[{"role":"user","content":"Reply with exactly: ok"}]}'

curl -fsS \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -X POST "http://127.0.0.1:8080/v1/responses" \
  --data '{"model":"'"${MODEL_NAME}"'","input":"Reply with exactly: ok"}'

curl -fsS \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -H "anthropic-version: 2023-06-01" \
  -X POST "http://127.0.0.1:8080/v1/messages" \
  --data '{"model":"'"${MODEL_NAME}"'","max_tokens":64,"messages":[{"role":"user","content":"Reply with exactly: ok"}]}'
```

7. metrics are visible and bounded

```bash
curl -fsS http://127.0.0.1:8080/metrics | grep gateway_http_requests_total
curl -fsS http://127.0.0.1:8080/metrics | grep gateway_source_resolution_total
curl -fsS http://127.0.0.1:8080/metrics | grep gateway_token_accounting_total
```

Release success criteria:

- all required checks pass on the target host or staging host with the same image and config shape
- `/readyz` confirms at least one configured upstream is healthy
- `/v1/models` confirms per-model availability for the models expected to serve traffic
- the three frozen compatibility paths return successful responses through the gateway
- `/metrics` exposes request, source-resolution, and token-accounting signals after traffic

### Rollback Playbook

Use rollback when the new image or config causes startup failure, readiness failure, widespread proxy errors,
or caller-visible regressions on the frozen compatibility paths.

Before changing production, keep these rollback inputs:

- previous working image tag
- previous `config.prod.yaml`
- previous `.env.prod`
- timestamp or commit for the attempted deployment

Rollback image only:

```bash
docker tag vllm-source-gateway:previous vllm-source-gateway:prod
docker compose -f docker-compose.prod.yml up -d
```

Rollback config or env:

```bash
cp config.prod.yaml.previous config.prod.yaml
cp .env.prod.previous .env.prod
docker compose -f docker-compose.prod.yml up -d --force-recreate
```

Rollback verification:

```bash
docker compose -f docker-compose.prod.yml ps
curl -fsS http://127.0.0.1:8080/livez
curl -fsS http://127.0.0.1:8080/readyz
curl -fsS http://127.0.0.1:8080/v1/models
curl -fsS http://127.0.0.1:8080/metrics | grep gateway_http_requests_total
```

Rollback decision guidance:

- rollback immediately if the container cannot stay running
- rollback if `/livez` fails after restart
- rollback if `/readyz` fails for all expected upstreams and the previous config was known healthy
- rollback if all three frozen compatibility paths fail through the gateway while direct upstream checks still pass
- debug in place only when the issue is isolated to one upstream, one department key, or one caller request shape

### Operator Runbook

Use this runbook for first-pass diagnosis before changing code.

Container is not running:

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs gateway --tail=200
docker inspect vllm-source-gateway
```

Likely causes:

- missing env-backed department keys or upstream tokens
- invalid `config.prod.yaml`
- host/runtime compatibility issue

`/livez` passes but `/readyz` fails:

```bash
curl -fsS http://127.0.0.1:8080/livez
curl -sS http://127.0.0.1:8080/readyz
docker compose -f docker-compose.prod.yml logs gateway --tail=200
```

Likely causes:

- upstream is down
- upstream bearer token is wrong
- `health.probe_path` is not supported by the upstream
- gateway host cannot route to upstream `base_url`

Upstream is back online but `/readyz` still fails:

```bash
curl -fsS http://127.0.0.1:8080/livez
curl -sS http://127.0.0.1:8080/readyz
curl -i http://UPSTREAM_HOST:UPSTREAM_PORT/v1/models
curl -i -H "Authorization: Bearer ${UPSTREAM_TOKEN}" http://UPSTREAM_HOST:UPSTREAM_PORT/v1/models
docker compose -f docker-compose.prod.yml logs gateway --tail=300
```

How to interpret:

- `/livez=200` and `/readyz=503` means the gateway process is alive but no upstream is currently marked healthy
- `/readyz` includes per-upstream diagnostics such as `last_probe_at`, `last_success_at`, `last_status_code`, and `last_error`
- if direct upstream curl succeeds but `/readyz` remains unhealthy after at least one `health.check_interval_seconds`, compare the gateway `health.probe_path`, upstream bearer token, and configured `base_url`
- if `last_probe_at` stops changing, the health monitor may be stuck or stopped and the gateway should be restarted while logs are preserved for debugging

`/v1/models` lists a model but requests fail:

```bash
curl -sS http://127.0.0.1:8080/v1/models
curl -sS http://127.0.0.1:8080/metrics | grep gateway_http_request_failures_total
docker compose -f docker-compose.prod.yml logs gateway --tail=200
```

Likely causes:

- upstream model name differs from the public model name in config
- selected upstream is unhealthy between probe intervals
- upstream rejects gateway bearer auth
- caller request shape is rejected by upstream

Caller is rejected or attributed to `unknown`:

```bash
grep -E '^DEPT_.*_API_KEYS=' .env.prod | cut -d= -f1
grep reject_unknown_api_keys config.prod.yaml
curl -sS http://127.0.0.1:8080/metrics | grep gateway_source_resolution_total
```

Likely causes:

- caller used the wrong `x-api-key`
- department env var is missing the key
- `security.reject_unknown_api_keys=true` is enabled

Metrics exist but token counters do not increase:

```bash
curl -sS http://127.0.0.1:8080/metrics | grep gateway_token_accounting_total
curl -sS http://127.0.0.1:8080/metrics | grep gateway_prompt_tokens_total
curl -sS http://127.0.0.1:8080/metrics | grep gateway_generation_tokens_total
```

Likely causes:

- upstream did not return reliable usage
- request failed or returned non-2xx
- stream completed without usage events

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
curl http://127.0.0.1:8080/models
```

### Post-Deploy Validation Checklist

After `docker compose up -d`, validate in this order:

1. container status

```bash
docker compose -f docker-compose.prod.yml ps
```

Optional container health detail:

```bash
docker inspect --format '{{json .State.Health}}' vllm-source-gateway
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
curl -sS http://127.0.0.1:8080/models
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

7. Anthropic-compatible request path

```bash
curl -sS \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -H "anthropic-version: 2023-06-01" \
  -X POST "http://127.0.0.1:8080/v1/messages" \
  --data '{
    "model": "gemma-4-31b",
    "max_tokens": 128,
    "messages": [{"role": "user", "content": "Reply with exactly: hello from gateway"}]
  }'
```

8. metrics contract visibility

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
- container is `unhealthy` while `/readyz` may still vary
  - inspect `docker inspect --format '{{json .State.Health}}' <container_id>`
  - verify `/livez` returns `200` from inside the deployment network path
- older hosts show container `exit=139`
  - treat this as a runtime compatibility issue first
  - move the tested image to a newer host rather than debugging application logic first

For one real-upstream validation flow using `gemma-4-31b`, see
[docs/e2e-validation-gemma4.md](/home/randy/Documents/crow/vllm-source-gateway/docs/e2e-validation-gemma4.md).

`/healthz` remains available as a backward-compatible alias for liveness.

### `/v1/messages` Maintenance Checklist

Use this checklist after upgrading vLLM, changing models, rotating upstreams, or handing the service to another maintainer.

Set convenient shell variables first:

```bash
export GATEWAY_BASE_URL=http://127.0.0.1:8080
export API_KEY=replace-me
export MODEL_NAME=replace-me
```

#### 1. Validate native `/v1/messages` streaming token accounting

Run one non-streaming `messages` request:

```bash
curl -sS \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -H "anthropic-version: 2023-06-01" \
  -X POST "${GATEWAY_BASE_URL}/v1/messages" \
  --data "{
    \"model\": \"${MODEL_NAME}\",
    \"max_tokens\": 128,
    \"messages\": [{\"role\": \"user\", \"content\": \"Reply with exactly: native messages path works.\"}]
  }"
```

Run one streaming `messages` request:

```bash
curl -sS -N \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -H "anthropic-version: 2023-06-01" \
  -X POST "${GATEWAY_BASE_URL}/v1/messages" \
  --data "{
    \"model\": \"${MODEL_NAME}\",
    \"max_tokens\": 128,
    \"stream\": true,
    \"messages\": [{\"role\": \"user\", \"content\": \"Count to 3 in one short line.\"}]
  }"
```

Inspect metrics after the requests:

```bash
curl -sS "${GATEWAY_BASE_URL}/metrics" | grep 'gateway_http_requests_total{department='
curl -sS "${GATEWAY_BASE_URL}/metrics" | grep 'endpoint="messages"'
curl -sS "${GATEWAY_BASE_URL}/metrics" | grep 'gateway_token_accounting_total'
curl -sS "${GATEWAY_BASE_URL}/metrics" | grep 'gateway_prompt_tokens_total'
curl -sS "${GATEWAY_BASE_URL}/metrics" | grep 'gateway_generation_tokens_total'
```

Success criteria:

- `gateway_http_requests_total{endpoint="messages",status_class="2xx"}` increases
- `gateway_token_accounting_total{endpoint="messages",accounting_status="recorded"}` appears when upstream exposes reliable usage
- `gateway_token_accounting_total{endpoint="messages",accounting_status="missing_usage"}` is acceptable only when upstream truly omits usage
- `gateway_prompt_tokens_total` and `gateway_generation_tokens_total` increase only when usage is actually present
- no unexpected `gateway_http_request_failures_total{endpoint="messages"}` is emitted

Current status:

- this validation has been completed successfully in production for the current upstream and model mix
- rerun it after changing vLLM version, model deployment shape, or upstream event behavior

#### 2. Validate production-like tool-use edge cases for `/v1/messages`

Run a tool-enabled non-streaming request:

```bash
curl -sS \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -H "anthropic-version: 2023-06-01" \
  -X POST "${GATEWAY_BASE_URL}/v1/messages" \
  --data "{
    \"model\": \"${MODEL_NAME}\",
    \"max_tokens\": 256,
    \"tools\": [{
      \"name\": \"get_weather\",
      \"description\": \"Return a fake weather report.\",
      \"input_schema\": {
        \"type\": \"object\",
        \"properties\": {
          \"city\": {\"type\": \"string\"}
        },
        \"required\": [\"city\"]
      }
    }],
    \"messages\": [{\"role\": \"user\", \"content\": \"What is the weather in Taipei? Use the tool.\"}]
  }"
```

Run a streaming tool-enabled request:

```bash
curl -sS -N \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -H "anthropic-version: 2023-06-01" \
  -X POST "${GATEWAY_BASE_URL}/v1/messages" \
  --data "{
    \"model\": \"${MODEL_NAME}\",
    \"max_tokens\": 256,
    \"stream\": true,
    \"tools\": [{
      \"name\": \"get_weather\",
      \"description\": \"Return a fake weather report.\",
      \"input_schema\": {
        \"type\": \"object\",
        \"properties\": {
          \"city\": {\"type\": \"string\"}
        },
        \"required\": [\"city\"]
      }
    }],
    \"messages\": [{\"role\": \"user\", \"content\": \"What is the weather in Taipei? Use the tool.\"}]
  }"
```

Optional raw upstream comparison:

```bash
curl -sS \
  -H "content-type: application/json" \
  -H "authorization: Bearer ${UPSTREAM_TOKEN}" \
  -H "anthropic-version: 2023-06-01" \
  -X POST "${UPSTREAM_BASE_URL}/v1/messages" \
  --data "{
    \"model\": \"${MODEL_NAME}\",
    \"max_tokens\": 256,
    \"tools\": [{
      \"name\": \"get_weather\",
      \"description\": \"Return a fake weather report.\",
      \"input_schema\": {
        \"type\": \"object\",
        \"properties\": {
          \"city\": {\"type\": \"string\"}
        },
        \"required\": [\"city\"]
      }
    }],
    \"messages\": [{\"role\": \"user\", \"content\": \"What is the weather in Taipei? Use the tool.\"}]
  }"
```

Inspect metrics:

```bash
curl -sS "${GATEWAY_BASE_URL}/metrics" | grep 'gateway_http_requests_total{department='
curl -sS "${GATEWAY_BASE_URL}/metrics" | grep 'gateway_source_resolution_total'
curl -sS "${GATEWAY_BASE_URL}/metrics" | grep 'gateway_http_request_failures_total'
curl -sS "${GATEWAY_BASE_URL}/metrics" | grep 'endpoint="messages"'
```

Success criteria:

- tool-enabled requests succeed through the gateway without request-shape rewriting failures
- tool-related responses or stream events look materially the same as direct upstream behavior
- `gateway_source_resolution_total` increments for the expected department
- upstream-side tool schema errors are passed through as upstream-origin failures instead of being rewritten by the gateway
- no unexpected gateway-origin `4xx` or `5xx` appears for the happy path

Current status:

- this validation has been completed successfully in production for the current upstream and model mix
- rerun it after upgrading vLLM, enabling new tool-calling models, or changing upstream auth / routing behavior

### `/v1/responses` Production Validation and Tool-Use Parity Checklist

Use this checklist when `/v1/responses` is a real caller path for code agents and you want parity confidence comparable to `/v1/messages`.

Set the same shell variables first:

```bash
export GATEWAY_BASE_URL=http://127.0.0.1:8080
export API_KEY=replace-me
export MODEL_NAME=replace-me
```

#### 1. Validate `/v1/responses` non-streaming and streaming parity

Run one non-streaming `responses` request:

```bash
curl -sS \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -X POST "${GATEWAY_BASE_URL}/v1/responses" \
  --data "{
    \"model\": \"${MODEL_NAME}\",
    \"input\": \"Reply with exactly: responses path works.\"
  }"
```

Run one streaming `responses` request:

```bash
curl -sS -N \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -X POST "${GATEWAY_BASE_URL}/v1/responses" \
  --data "{
    \"model\": \"${MODEL_NAME}\",
    \"input\": \"Count to 3 in one short line.\",
    \"stream\": true
  }"
```

Inspect metrics:

```bash
curl -sS "${GATEWAY_BASE_URL}/metrics" | grep 'endpoint="responses"'
curl -sS "${GATEWAY_BASE_URL}/metrics" | grep 'gateway_http_requests_total{department='
curl -sS "${GATEWAY_BASE_URL}/metrics" | grep 'gateway_token_accounting_total'
curl -sS "${GATEWAY_BASE_URL}/metrics" | grep 'gateway_prompt_tokens_total'
curl -sS "${GATEWAY_BASE_URL}/metrics" | grep 'gateway_generation_tokens_total'
```

Success criteria:

- `gateway_http_requests_total{endpoint="responses",status_class="2xx"}` increases
- `gateway_token_accounting_total{endpoint="responses"}` shows either `recorded` or `missing_usage` in a way that matches real upstream behavior
- if upstream exposes reliable usage, `gateway_prompt_tokens_total` and `gateway_generation_tokens_total` increase
- if upstream omits usage, the gateway falls back to `missing_usage` without guessing token counts
- no unexpected `gateway_http_request_failures_total{endpoint="responses"}` is emitted

Current status:

- this validation has been completed successfully in production for the current upstream and model mix
- rerun it after upgrading vLLM, changing `/v1/responses` callers, or modifying upstream event behavior

#### 2. Validate `/v1/responses` tool-use parity

Run a tool-enabled non-streaming `responses` request:

```bash
curl -sS \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -X POST "${GATEWAY_BASE_URL}/v1/responses" \
  --data "{
    \"model\": \"${MODEL_NAME}\",
    \"input\": \"What is the weather in Taipei? Use the tool.\",
    \"tools\": [{
      \"type\": \"function\",
      \"name\": \"get_weather\",
      \"description\": \"Return a fake weather report.\",
      \"parameters\": {
        \"type\": \"object\",
        \"properties\": {
          \"city\": {\"type\": \"string\"}
        },
        \"required\": [\"city\"]
      }
    }]
  }"
```

Run a streaming tool-enabled `responses` request:

```bash
curl -sS -N \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -X POST "${GATEWAY_BASE_URL}/v1/responses" \
  --data "{
    \"model\": \"${MODEL_NAME}\",
    \"input\": \"What is the weather in Taipei? Use the tool.\",
    \"stream\": true,
    \"tools\": [{
      \"type\": \"function\",
      \"name\": \"get_weather\",
      \"description\": \"Return a fake weather report.\",
      \"parameters\": {
        \"type\": \"object\",
        \"properties\": {
          \"city\": {\"type\": \"string\"}
        },
        \"required\": [\"city\"]
      }
    }]
  }"
```

Optional raw upstream comparison:

```bash
curl -sS \
  -H "content-type: application/json" \
  -H "authorization: Bearer ${UPSTREAM_TOKEN}" \
  -X POST "${UPSTREAM_BASE_URL}/v1/responses" \
  --data "{
    \"model\": \"${MODEL_NAME}\",
    \"input\": \"What is the weather in Taipei? Use the tool.\",
    \"tools\": [{
      \"type\": \"function\",
      \"name\": \"get_weather\",
      \"description\": \"Return a fake weather report.\",
      \"parameters\": {
        \"type\": \"object\",
        \"properties\": {
          \"city\": {\"type\": \"string\"}
        },
        \"required\": [\"city\"]
      }
    }]
  }"
```

Inspect metrics:

```bash
curl -sS "${GATEWAY_BASE_URL}/metrics" | grep 'endpoint="responses"'
curl -sS "${GATEWAY_BASE_URL}/metrics" | grep 'gateway_source_resolution_total'
curl -sS "${GATEWAY_BASE_URL}/metrics" | grep 'gateway_http_request_failures_total'
```

Success criteria:

- tool-enabled `/v1/responses` requests succeed without gateway-side request-shape rewriting failures
- streamed or non-streamed tool-related response shapes look materially the same as direct upstream behavior
- `gateway_source_resolution_total` increments for the expected department
- upstream-side tool schema errors are passed through as upstream-origin failures instead of being rewritten by the gateway
- no unexpected gateway-origin `4xx` or `5xx` appears for the happy path

Current status:

- this validation has been completed successfully in production for the current upstream and model mix
- rerun it after enabling new tool-calling models, changing `/v1/responses` clients, or modifying upstream auth / routing behavior

#### 3. Reconfirm compatibility freeze and stability-first priority

This is a release or maintenance review step rather than a single curl command.

Collect evidence from current traffic and validations:

```bash
curl -sS "${GATEWAY_BASE_URL}/metrics" | grep 'endpoint="chat_completions"'
curl -sS "${GATEWAY_BASE_URL}/metrics" | grep 'endpoint="responses"'
curl -sS "${GATEWAY_BASE_URL}/metrics" | grep 'endpoint="messages"'
curl -sS "${GATEWAY_BASE_URL}/metrics" | grep 'gateway_http_request_failures_total'
curl -sS "${GATEWAY_BASE_URL}/metrics" | grep 'gateway_token_accounting_total'
```

Decision checklist:

- keep `POST /v1/chat/completions`, `POST /v1/responses`, and `POST /v1/messages` as the only supported compatibility paths for the current launch cycle
- prioritize reliability, rollback safety, observability correctness, and operator runbooks before expanding the gateway contract
- if a new caller population depends on another API surface, document that demand explicitly and treat it as a separate post-launch decision
- if the main remaining issues are metrics confidence, deployment safety, or incident handling, invest there before adding another compatibility path

Success criteria:

- the compatibility freeze is explicit in maintainer documentation
- stability-first follow-up work is chosen based on observed failures, operational gaps, and maintainer evidence rather than guesswork

## Interop Contract with `vllm-usage-observability`

This repo must expose raw metrics that the observability repo can consume as stable inputs.

Security posture for `/metrics`:

- `/metrics` is intended for internal Prometheus scrape traffic
- do not treat unauthenticated public exposure of `/metrics` as an accepted production posture
- if stronger protection is needed, enforce it at the deployment or network boundary

Observability scope:

- the current bounded metrics contract is optimized for source-attributed proxy traffic
- `/v1/chat/completions`, `/v1/responses`, and `/v1/messages` are the primary routes expected to feed department-level request, token, and failure views
- `/v1/models`, `/livez`, `/readyz`, `/healthz`, `/metrics`, generic `404` responses, and some pre-proxy failures are not currently first-class parts of the source-attributed metrics contract
- access logging follows the same bias toward operationally relevant proxy traffic
- admission-control phase 1 exposes enforcement and safety signals such as rejections, admitted
  in-flight requests, token-budget rejections, and retry-guard openings
- capacity-tuning observability is phase 2: gateway-level upstream request duration, stream first
  chunk latency, and upstream selection counters are not yet emitted
- current gateway metrics can show who is admitted or rejected, but they cannot by themselves identify
  which upstream has degraded latency or TTFT

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
- admission-control concurrency, retry-guard, and token-budget state is also process-local; keep production
  admission-control deployments on one uvicorn worker
- admission-control model and department limits are upstream-admission controls: requests must be
  read and parsed before the gateway knows the target model, so these limits protect vLLM queues but
  do not cap concurrent body reads or JSON parsing inside the gateway
- v1 admission-control fairness is department-level: it can protect other departments from a noisy
  department, but it does not separate human interactive traffic from coding-agent or batch traffic
  inside the same department bucket
- use separate bounded department identities such as `data_platform_human` and `data_platform_agent`
  for immediate human/agent separation; a dedicated bounded `workload_class` dimension is future work
- `department="unknown"` is fallback attribution, not a configured department; department-scoped
  admission limits cannot target it
- use `security.reject_unknown_api_keys=true` to reject unknown callers, or `global_model_limits`
  to protect a model pool from all callers including unknown traffic
- `admission_control.enabled: false` allows draft admission references; cross-reference validation
  runs only when admission control is enabled, so validate enabled configs before rollout
- protect the gateway ingress separately with body-size, connection, request-rate, and timeout limits
  at the reverse proxy or load balancer when large-body floods are a concern
- treat multi-worker or horizontally scaled shared-state support as future architecture work, not a current guarantee
- use shared state such as Redis before treating these limits as global across workers or replicas

Representative starting point:

- `4-8 vCPU`
- `8-16 GB RAM`

Final sizing depends more on concurrency, streaming duration, and network throughput than on model compute.

### Single-Process Capacity Baseline

Use the capacity baseline script to characterize the current single-process deployment under
expected streaming and agent-style concurrency. This is a lightweight streaming
production-confidence check, not a maximum throughput benchmark and not a separate non-streaming
capacity profile.

Run from a host that can reach the gateway:

```bash
export GATEWAY_BASE_URL=http://127.0.0.1:8080
export API_KEY=replace-me
export MODEL_NAME=replace-me

python tools/capacity_baseline.py \
  --paths chat,responses,messages \
  --concurrency-levels 1,5,10 \
  --requests-per-level 10 \
  --output-dir /tmp/vllm-source-gateway-capacity
```

Useful options:

- `--paths chat,responses,messages` selects the frozen proxy paths to test
- `--concurrency-levels 1,5,10,20` sets the concurrent streaming levels
- `--requests-per-level 20` controls sample size per path and concurrency level
- `--mixed-models model-a:10,model-b:5` runs multiple models concurrently in one baseline
- `--prompt-file /path/to/prompt.txt` reads a UTF-8 prompt file for large-context runs
- `--fail-on-error` returns a non-zero exit code when any request fails

For mixed-model baselines, use `--mixed-models` to exercise production-like traffic across
multiple model routes at the same time:

```bash
python tools/capacity_baseline.py \
  --paths chat,responses,messages \
  --mixed-models model-a:10,model-b:5 \
  --requests-per-level 30 \
  --timeout-seconds 300 \
  --max-tokens 1024 \
  --output-dir /tmp/vllm-source-gateway-capacity-mixed
```

Mixed-model mode intentionally overrides `--model` and `--concurrency-levels`. The summary report
groups results by model and path so maintainers can check whether one busy model affects another.
For accepted baseline interpretation and operational checks, use the production baseline document
linked below.

For large-context baselines, prefer `--prompt-file` instead of shell command substitution:

```bash
python tools/capacity_baseline.py \
  --paths chat,responses,messages \
  --concurrency-levels 1,3,5 \
  --requests-per-level 10 \
  --timeout-seconds 900 \
  --max-tokens 1024 \
  --prompt-file /tmp/large-context-prompt.txt \
  --output-dir /tmp/vllm-source-gateway-capacity-large-context
```

When recording large-context results, capture both prompt characters and observed prompt tokens if
the upstream returns reliable usage. Character count is useful for reproducing the input shape, but
prompt tokens are the better comparison unit across models, tokenizers, and languages.

Artifacts:

- `summary.md`
- `summary.json`
- `results.json`
- `metrics_before.prom`
- `metrics_after.prom`

Baseline success criteria:

- no unexpected gateway-origin failures
- no container restart during the run
- `/livez` and `/readyz` remain healthy after the run
- memory does not show obvious continuous growth
- `summary.md` records an acceptable success rate and latency range for the intended deployment

For accepted production baselines, also record run identity and operational evidence:

- gateway image tag or git revision
- vLLM version
- production host shape
- gateway config revision or checksum
- run date
- `/livez` and `/readyz` snapshots after the run
- container restart counter and basic CPU/memory observation
- token-accounting status and observed prompt tokens for large-context profiles when available

Accepted production capacity baseline currently exists for:

- single-model normal, long-output, and large-context profiles
- mixed-model normal, long-output, and large-context profiles
- frozen API paths: `chat/completions`, `responses`, and `messages`

See
[docs/capacity-baseline-production-model-mix.md](docs/capacity-baseline-production-model-mix.md)
for the current production baseline summary.

## MVP Scope

The MVP should stay intentionally small.

### In Scope

- one gateway service
- read-only `GET /v1/models`
- read-only human catalog `GET /models`
- support for `/v1/chat/completions`
- support for `/v1/responses`
- support for `POST /v1/messages` as a native upstream proxy path
- static upstream list for multiple vLLM services
- streaming pass-through
- source resolution from API key
- department-level Prometheus metrics
- basic timeout handling
- liveness and readiness endpoints
- configuration-driven department mapping

### Out of Scope

- streaming-specific advanced accounting beyond what is needed for correct request completion metrics
- full Anthropic Messages API compatibility beyond upstream `POST /v1/messages` and the current stateless proxy scope
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
4. Anthropic-oriented callers can call the gateway through the native `POST /v1/messages` proxy path
5. one department using multiple IPs still appears as one department in Prometheus metrics
6. the observability repo can derive department-level request, token, error-rate, and latency views from the emitted metrics
7. `department="unknown"` is visible and explainable

One successful real-upstream validation record is documented in
[docs/e2e-validation-gemma4.md](docs/e2e-validation-gemma4.md).

## Operational Risks

- the gateway becomes a new failure point if timeouts and health handling are weak
- API-key ownership can drift if keys are shared or poorly governed
- token accounting can become incomplete if upstream usage is missing or parsed inconsistently

Historical reviewer-driven launch hardening decisions are recorded in
[docs/launch-hardening-record.md](docs/launch-hardening-record.md).

## Recommended Evolution Path

After the MVP is stable, future phases may add:

- stronger auth integration
- richer routing and failover behavior
- limited rate limiting
- event/log-based attribution for high-cardinality or audit-heavy use cases

Those phases should be added only after the thin gateway boundary is working and observed in production-like environments.
