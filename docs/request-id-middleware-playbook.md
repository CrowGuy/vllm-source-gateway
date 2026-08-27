# vLLM Request ID Middleware Playbook

This playbook documents the production-validated volume mount approach for
adding request-id logging to a vLLM container.

Use it when gateway logs include `request_id`, but vLLM Docker logs cannot be
grepped by the same `X-Request-Id`.

## What This Adds

The middleware logs one line per vLLM API request with:

- `request_id` from the incoming `X-Request-Id` header
- method and path
- status code
- request duration

The middleware does not read the request body, log prompts, log API keys, or log
model outputs.

## Preconditions

- vLLM is started with `--enable-request-id-headers`.
- Callers or the gateway send `X-Request-Id`.
- The gateway forwards `x-request-id` upstream.
- This repository is available on the host running the vLLM container.

The middleware source is:

```text
tools/request_id_middleware.py
```

Keep that file as the single source of truth. Do not copy the middleware code
into deployment scripts unless the deployment target cannot mount this repo.

## Docker Run

Mount the middleware file into the vLLM container and put its directory on
`PYTHONPATH`:

```bash
docker run --gpus all \
  -v "$PWD/tools/request_id_middleware.py:/app/middleware/request_id_middleware.py:ro" \
  -e PYTHONPATH=/app/middleware \
  VLLM_IMAGE \
  vllm serve MODEL_NAME_OR_PATH \
    --enable-request-id-headers \
    --middleware request_id_middleware.RequestIdLogMiddleware \
    --uvicorn-log-level info
```

Replace:

- `VLLM_IMAGE` with the deployed vLLM image
- `MODEL_NAME_OR_PATH` with the model path or model id used by that vLLM
  instance

Keep the container path and module name aligned:

```text
/app/middleware/request_id_middleware.py
PYTHONPATH=/app/middleware
--middleware request_id_middleware.RequestIdLogMiddleware
```

## Docker Compose

Example service shape:

```yaml
services:
  vllm:
    image: VLLM_IMAGE
    volumes:
      - ./tools/request_id_middleware.py:/app/middleware/request_id_middleware.py:ro
    environment:
      PYTHONPATH: /app/middleware
    command:
      - vllm
      - serve
      - MODEL_NAME_OR_PATH
      - --enable-request-id-headers
      - --middleware
      - request_id_middleware.RequestIdLogMiddleware
      - --uvicorn-log-level
      - info
```

Preserve any existing vLLM model, tensor parallel, scheduler, auth, and memory
flags from the production service. This playbook only adds the middleware
mount, `PYTHONPATH`, and middleware CLI flag.

## Validation

Set a request id:

```bash
export REQ_ID="debug-vllm-mw-$(date -u +%Y%m%dT%H%M%SZ)"
```

Send a request through the gateway:

```bash
curl -sS -D /tmp/gateway-headers.txt -o /tmp/gateway-body.json \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -H "x-request-id: ${REQ_ID}" \
  -X POST "${GATEWAY_BASE_URL}/v1/chat/completions" \
  --data '{
    "model": "'"${MODEL_NAME}"'",
    "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
    "max_tokens": 8
  }'
```

Confirm the gateway log can find the request:

```bash
docker logs GATEWAY_CONTAINER 2>&1 | grep "${REQ_ID}"
```

Confirm the vLLM log can find the same request:

```bash
docker logs VLLM_CONTAINER 2>&1 | grep "${REQ_ID}"
```

Expected vLLM log shape:

```text
vllm request completed request_id=debug-vllm-mw-... method=POST path=/v1/chat/completions status_code=200 duration_seconds=...
```

As an additional signal, direct vLLM responses may include the request id in the
response body id, for example:

```json
{"id":"chatcmpl-debug-vllm-mw-..."}
```

Treat that as a helpful confirmation that vLLM received the header. The
middleware log line is still the operational evidence needed for Docker log
correlation.

## Troubleshooting

`ModuleNotFoundError: request_id_middleware`

- Confirm the host file exists at `tools/request_id_middleware.py`.
- Confirm the container mount path is `/app/middleware/request_id_middleware.py`.
- Confirm `PYTHONPATH=/app/middleware` is set inside the vLLM container.

vLLM starts, but no request-id lines appear:

- Confirm the vLLM command includes
  `--middleware request_id_middleware.RequestIdLogMiddleware`.
- Confirm the request includes `X-Request-Id`.
- Confirm the request is reaching this vLLM container, not another upstream.
- Confirm `--uvicorn-log-level info` or an equivalent logging level is enabled.

Gateway logs show `request_id`, but vLLM logs do not:

- Confirm the gateway is forwarding `x-request-id`.
- Confirm the selected upstream for that request is the vLLM container with the
  middleware mounted.
- Grep by the actual request id value, not the literal string `x-request-id`.

vLLM response id includes `chatcmpl-<request-id>`, but vLLM logs do not:

- `--enable-request-id-headers` is working.
- The middleware is not loaded, logging below `INFO`, or the request is going to
  a different vLLM container.

Request appears in vLLM logs but not gateway logs:

- Confirm the request was sent through the gateway rather than directly to
  vLLM.
- Confirm the gateway container name used in `docker logs` is correct.

## Security Boundary

This middleware intentionally logs only request metadata. It does not inspect or
store:

- prompts
- messages
- tool payloads
- API keys or bearer tokens
- generated outputs

Do not add prompt/body logging to this middleware for production debugging.
