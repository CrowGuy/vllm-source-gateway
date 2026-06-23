# Gemma4 E2E Validation

This document provides the smallest real end-to-end validation flow for `vllm-source-gateway`
using one reachable vLLM upstream that serves `gemma-4-31b`.

## Environment Setup

Create and activate a virtual environment with `uv`:

```bash
uv venv
source .venv/bin/activate
```

Install runtime dependencies:

```bash
uv sync
```

Install development dependencies when you also want pytest or related tooling:

```bash
uv sync --extra dev
```

## Assumptions

- The upstream vLLM service is reachable from the machine that runs the gateway.
- The upstream supports:
  - `POST /v1/chat/completions`
  - `POST /v1/responses`
- The recorded validation run in this document predates native `/v1/messages` proxy validation and therefore does not yet serve as real-upstream proof for Anthropic-compatible ingress behavior.
- The gateway and vLLM are on the same host if you use `http://127.0.0.1:8000`.
- The gateway Python dependencies are installed locally.

## Files

- [`config.e2e.gemma4.yaml`](/home/randy/Documents/crow/vllm-source-gateway/config.e2e.gemma4.yaml)
- [`e2e_validate_gemma4.sh`](/home/randy/Documents/crow/vllm-source-gateway/e2e_validate_gemma4.sh)

## Validation Inputs

- upstream model: `gemma-4-31b`
- config file: `config.e2e.gemma4.yaml`
- validation script: `e2e_validate_gemma4.sh`
- upstream target must use `http://127.0.0.1:8000` or another real reachable host, not
  `http://0.0.0.0:8000`

## Important URL Note

Do not use `http://0.0.0.0:8000` as an upstream client target.

- `0.0.0.0` is a bind address, not a routable destination.
- If gateway and vLLM run on the same machine, use `http://127.0.0.1:8000`.
- If they run on different machines, replace the config `base_url` with the real host IP or DNS name.

## What This Validation Covers

The validation script checks:

1. direct upstream smoke checks for chat and responses
2. gateway startup with the E2E config
3. `GET /healthz`
4. `GET /v1/models`
5. non-streaming `POST /v1/chat/completions`
6. non-streaming `POST /v1/responses`
7. `/metrics` scrape and bounded-label checks
8. streaming chat pass-through
9. streaming responses pass-through
10. unknown department fallback

Current omission:

- this recorded E2E flow does **not yet** validate `POST /v1/messages`
- once native `/v1/messages` proxying is validated against a real upstream, this document should either be extended or split into a dedicated messages-focused E2E record

## How To Run

Make the script executable:

```bash
chmod +x ./e2e_validate_gemma4.sh
```

Run with defaults:

```bash
./e2e_validate_gemma4.sh
```

Run with overrides:

```bash
UPSTREAM_BASE_URL=http://127.0.0.1:8000 \
GATEWAY_CONFIG=./config.e2e.gemma4.yaml \
MODEL_NAME=gemma-4-31b \
VALIDATION_API_KEY=validation-key \
./e2e_validate_gemma4.sh
```

## Validation Result

This validation run completed successfully.

Validated successfully:

- upstream chat success
- upstream responses success
- gateway startup success
- `GET /v1/models` success
- non-streaming chat success
- non-streaming responses success
- metrics emitted
- streaming chat success
- streaming responses success
- unknown department fallback success

## Result Interpretation

This result means the MVP core request path has been validated against a real upstream service,
not only mocks or unit tests.

It also confirms that token accounting observed reliable usage during the validation run, because
the metrics scrape detected prompt token counters from real traffic.

This validation does not mean the gateway has already been verified for:

- multi-upstream round-robin
- failover behavior
- Docker packaging
- production deployment topology
- timeout or failure injection scenarios
- real-upstream native `/v1/messages` behavior

## Expected Outputs

- gateway starts on `127.0.0.1:8080`
- `GET /v1/models` includes `gemma-4-31b`
- non-streaming chat and responses both return `200`
- streaming chat and responses both produce SSE `data:` lines
- `/metrics` contains:
  - `gateway_http_requests_total`
  - `gateway_source_resolution_total`
  - `gateway_token_accounting_total`
- an unmapped API key produces:
  - `department="unknown"`
  - `resolution_source="unknown"`

## Notes

- If upstream usage is present, the gateway should emit prompt/generation token counters.
- If upstream usage is missing, the gateway should emit `gateway_token_accounting_total` with
  `accounting_status="missing_usage"` instead of guessing token totals.
- All validation artifacts are written to `/tmp/vllm-source-gateway-e2e` unless overridden.

## Artifacts

Default validation artifacts are written to:

- `/tmp/vllm-source-gateway-e2e`

Useful files to inspect during debugging include:

- `gateway.log`
- `upstream_chat.json`
- `upstream_responses.json`
- `gateway_chat.json`
- `gateway_responses.json`
- `gateway_chat_stream.txt`
- `gateway_responses_stream.txt`
- `metrics_final.prom`

## Known Limits

This validation did not cover:

- multi-upstream round-robin
- timeout injection
- failure injection
- containerized deployment path
- `POST /v1/messages` against a real upstream

## Next `/v1/messages` Validation Checklist

The next real-upstream validation pass should add:

1. direct upstream `POST /v1/messages` smoke check
2. gateway `POST /v1/messages` non-streaming smoke check
3. gateway `POST /v1/messages` streaming smoke check
4. at least one tool-use request through gateway `POST /v1/messages`
5. metrics scrape after `messages` requests to confirm bounded labels and conservative accounting
