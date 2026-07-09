# Same-Model Multi-Upstream Routing Validation Playbook

This playbook validates same-model multi-upstream routing behavior for the gateway.

It covers two separate checks:

- round-robin distribution across healthy upstreams for the same public model
- connect-stage failover when the selected upstream cannot be reached before any response starts

Use temporary gateway instances for these checks when possible. Do not intentionally break the
primary production gateway while users are sending traffic.

## What This Validates

Round-robin validation confirms:

- one public model is configured with at least two healthy upstreams
- repeated requests for that model are distributed across the same-model upstream pool
- all frozen proxy paths can route through the same model pool
- no unexpected gateway-origin failures occur during normal routing

Connect-stage failover validation confirms:

- one public model is configured with at least two upstreams
- the first selected upstream fails during connection setup
- another upstream for the same model remains reachable
- the gateway retries the same request against the second upstream
- the caller receives a successful response instead of an immediate `502` or `504`

Failover validation does not validate generic retry behavior. The gateway should not treat
upstream non-2xx responses, read/generation timeouts, or already-started streaming responses as
generic failover cases.

## Round-Robin Validation

Use a config with at least two healthy upstreams serving the same gateway-facing model id.

Example shape:

```yaml
health:
  enabled: true
  probe_path: "/v1/models"

upstreams:
  - name: "model-a"
    base_url: "http://UPSTREAM_A_HOST:8000"
    authorization_from_env: "UPSTREAM_MODEL_A_TOKEN"
    models:
      - "YOUR_MODEL_NAME"

  - name: "model-b"
    base_url: "http://UPSTREAM_B_HOST:8000"
    authorization_from_env: "UPSTREAM_MODEL_B_TOKEN"
    models:
      - "YOUR_MODEL_NAME"
```

Replace `YOUR_MODEL_NAME` with the exact gateway-facing model id shown by `GET /v1/models`.
If an upstream does not require bearer auth, remove `authorization_from_env`.

### Pre-Checks

Set shell variables:

```bash
export GATEWAY_BASE_URL=http://127.0.0.1:8080
export API_KEY=replace-with-validation-key
export MODEL_NAME=replace-with-your-model-name
```

Confirm both upstreams are healthy from the gateway's point of view:

```bash
curl -sS "${GATEWAY_BASE_URL}/readyz"
curl -sS "${GATEWAY_BASE_URL}/v1/models"
```

Success precondition:

- `/readyz` shows both same-model upstreams as healthy
- `/v1/models` shows the expected total and healthy upstream count for the model

### Requests

Send repeated short requests through each frozen proxy path.

Chat:

```bash
for i in $(seq 1 10); do
  curl -sS \
    -H "content-type: application/json" \
    -H "x-api-key: ${API_KEY}" \
    -X POST "${GATEWAY_BASE_URL}/v1/chat/completions" \
    --data "{
      \"model\": \"${MODEL_NAME}\",
      \"messages\": [{\"role\": \"user\", \"content\": \"Reply with exactly: rr chat ${i}\"}],
      \"max_tokens\": 32
    }" >/dev/null
done
```

Responses:

```bash
for i in $(seq 1 10); do
  curl -sS \
    -H "content-type: application/json" \
    -H "x-api-key: ${API_KEY}" \
    -X POST "${GATEWAY_BASE_URL}/v1/responses" \
    --data "{
      \"model\": \"${MODEL_NAME}\",
      \"input\": \"Reply with exactly: rr responses ${i}\",
      \"max_output_tokens\": 32
    }" >/dev/null
done
```

Messages:

```bash
for i in $(seq 1 10); do
  curl -sS \
    -H "content-type: application/json" \
    -H "x-api-key: ${API_KEY}" \
    -H "anthropic-version: 2023-06-01" \
    -X POST "${GATEWAY_BASE_URL}/v1/messages" \
    --data "{
      \"model\": \"${MODEL_NAME}\",
      \"max_tokens\": 32,
      \"messages\": [{\"role\": \"user\", \"content\": \"Reply with exactly: rr messages ${i}\"}]
    }" >/dev/null
done
```

### Evidence To Capture

Capture gateway and upstream evidence:

```bash
curl -sS "${GATEWAY_BASE_URL}/readyz" > round-robin-readyz.json
curl -sS "${GATEWAY_BASE_URL}/v1/models" > round-robin-models.json
curl -sS "${GATEWAY_BASE_URL}/metrics" > round-robin-metrics.prom
```

Also capture either:

- gateway structured logs showing selected upstreams, if available
- upstream access logs from both upstreams
- per-upstream request counters from the upstream observability stack

### Round-Robin Success Criteria

Round-robin validation passes when:

- all tested caller requests return `2xx`
- at least two same-model upstreams receive traffic
- traffic distribution is plausibly round-robin for repeated short requests
- `/readyz` and `/v1/models` report the expected healthy upstream count
- no unexpected gateway-origin `5xx` failures appear for the validation window

This validation does not require a perfect 50/50 split if concurrent user traffic is also hitting
the same gateway. It should still show that more than one same-model upstream is used.

## Connect-Stage Failover Validation

To test connect-stage failover, the first upstream must still be considered healthy by the
routing registry when the request starts.

For this reason, the validation config intentionally disables active health checks:

```yaml
health:
  enabled: false
```

If active health checks are enabled, the gateway may mark the dead upstream unhealthy before
the request is sent. That would validate health-based routing, not connect-stage failover.

## Temporary Failover Validation Config

Create a temporary config such as `config.failover-validation.yaml`.

The dead upstream must appear first in the model pool so the first request selects it before
failing over to the healthy upstream.

```yaml
server:
  host: "0.0.0.0"
  port: 8080
  max_request_body_bytes: 4194304
  max_sse_decode_buffer_bytes: 262144

timeouts:
  connect_seconds: 2
  upstream_request_seconds: 120
  stream_idle_seconds: 30

health:
  enabled: false
  probe_path: "/v1/models"
  check_interval_seconds: 15
  request_timeout_seconds: 3

routing:
  strategy: "round_robin"

security:
  reject_unknown_api_keys: false

upstreams:
  - name: "dead-a"
    base_url: "http://127.0.0.1:9"
    models:
      - "YOUR_MODEL_NAME"

  - name: "healthy-b"
    base_url: "http://REAL_UPSTREAM_HOST:8000"
    authorization_from_env: "UPSTREAM_HEALTHY_B_TOKEN"
    models:
      - "YOUR_MODEL_NAME"

departments:
  validation:
    api_keys_from_env: "DEPT_VALIDATION_API_KEYS"
```

Notes:

- `127.0.0.1:9` is intentionally unreachable from inside the gateway container in most
  deployments and should produce a connect-stage failure quickly.
- Replace `YOUR_MODEL_NAME` with the gateway-facing model id.
- Replace `REAL_UPSTREAM_HOST:8000` with a reachable vLLM upstream serving the same model.
- If the healthy upstream does not require bearer auth, remove `authorization_from_env`.

## Start A Temporary Failover Gateway

Use a different host port from production, for example `18080`.

```bash
docker run --rm \
  --name vllm-source-gateway-failover-validation \
  --env-file .env.prod \
  -e VLLM_SOURCE_GATEWAY_CONFIG=/app/config.failover-validation.yaml \
  -p 18080:8080 \
  -v "$PWD/config.failover-validation.yaml:/app/config.failover-validation.yaml:ro" \
  vllm-source-gateway:prod
```

In another shell, set:

```bash
export GATEWAY_BASE_URL=http://127.0.0.1:18080
export API_KEY=replace-with-validation-key
export MODEL_NAME=replace-with-your-model-name
```

## Failover Pre-Checks

Confirm the temporary gateway is alive:

```bash
curl -fsS "${GATEWAY_BASE_URL}/livez"
```

Confirm readiness reports both upstreams as initially healthy:

```bash
curl -sS "${GATEWAY_BASE_URL}/readyz"
```

With `health.enabled=false`, readiness reflects the routing registry's initial health state,
not real upstream reachability. This is expected for this validation.

Confirm the healthy upstream works directly from the host or deployment network:

```bash
curl -i -H "Authorization: Bearer ${UPSTREAM_HEALTHY_B_TOKEN}" \
  http://REAL_UPSTREAM_HOST:8000/v1/models
```

Omit the `Authorization` header if that upstream does not require bearer auth.

## Failover Requests

Run at least one non-streaming request through each frozen proxy path.

### Chat Completions

```bash
curl -sS \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -X POST "${GATEWAY_BASE_URL}/v1/chat/completions" \
  --data "{
    \"model\": \"${MODEL_NAME}\",
    \"messages\": [{\"role\": \"user\", \"content\": \"Reply with exactly: failover ok\"}],
    \"max_tokens\": 32
  }"
```

### Responses

```bash
curl -sS \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -X POST "${GATEWAY_BASE_URL}/v1/responses" \
  --data "{
    \"model\": \"${MODEL_NAME}\",
    \"input\": \"Reply with exactly: failover ok\",
    \"max_output_tokens\": 32
  }"
```

### Messages

```bash
curl -sS \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -H "anthropic-version: 2023-06-01" \
  -X POST "${GATEWAY_BASE_URL}/v1/messages" \
  --data "{
    \"model\": \"${MODEL_NAME}\",
    \"max_tokens\": 32,
    \"messages\": [{\"role\": \"user\", \"content\": \"Reply with exactly: failover ok\"}]
  }"
```

### Streaming Spot Check

Run at least one streaming request to confirm connect-stage failover also happens before a
stream starts:

```bash
curl -sS -N \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -X POST "${GATEWAY_BASE_URL}/v1/chat/completions" \
  --data "{
    \"model\": \"${MODEL_NAME}\",
    \"messages\": [{\"role\": \"user\", \"content\": \"Count to 3 in one short line\"}],
    \"max_tokens\": 64,
    \"stream\": true
  }"
```

## Failover Evidence To Capture

Capture these artifacts before stopping the temporary gateway:

```bash
curl -sS "${GATEWAY_BASE_URL}/readyz" > failover-readyz.json
curl -sS "${GATEWAY_BASE_URL}/metrics" > failover-metrics.prom
docker logs vllm-source-gateway-failover-validation > failover-gateway.log
```

Also capture healthy upstream access logs if available. They should show requests arriving
after the gateway first attempts the dead upstream.

## Failover Success Criteria

The validation passes when:

- all tested caller requests return `2xx`
- the healthy upstream receives and serves the requests
- the caller does not receive `502` or `504` for the connect-stage failure
- gateway metrics do not show final gateway-origin `5xx` failures for the validated requests
- token accounting remains conservative: `recorded` when usage is present, otherwise `missing_usage`

The validation does not pass if:

- the gateway skips the dead upstream because health checks already marked it unhealthy
- the caller receives `502` or `504` while the second upstream is healthy
- an upstream `4xx` or `5xx` response is counted as a successful failover case

## Cleanup

Stop the temporary gateway:

```bash
docker stop vllm-source-gateway-failover-validation
```

Remove the temporary config after saving any evidence needed for the validation record.
