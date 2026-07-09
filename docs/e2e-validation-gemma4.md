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
- This document records the original smallest real-upstream `gemma-4-31b` E2E flow.
- Additional real-upstream production validation for native `POST /v1/messages` and production-like `/v1/responses` tool-use parity has been completed separately after this script-backed run.
- The gateway and vLLM are on the same host if you use `http://127.0.0.1:8000`.
- The gateway Python dependencies are installed locally.

## Files

- [`config.e2e.gemma4.yaml`](config.e2e.gemma4.yaml)
- [`e2e_validate_gemma4.sh`](e2e_validate_gemma4.sh)

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

Current scope note:

- this script-backed `gemma-4-31b` flow remains focused on `chat/completions` and `responses`
- native `POST /v1/messages` production validation exists today, but it is documented as part of the current production validation and maintenance workflow rather than this older shell-script path

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

Additional real-upstream validation completed after this recorded run:

- native `POST /v1/messages` non-streaming and streaming behavior has been validated in production
- native `/v1/messages` streaming token accounting has been validated against real upstream behavior
- production-like tool-use edge-case validation has been completed for `/v1/messages`
- `/v1/responses` tool-use parity has also been validated for the active real-caller scenarios using that path
- human-readable `GET /models` model catalog has been deployed and validated in production

Additional `GET /models` production validation checklist:

- returns `text/html`
- displays configured `model_catalog` metadata for documented models
- reflects model availability from routing health state
- does not expose upstream IPs, upstream URLs, bearer tokens, or other topology-sensitive details

## Result Interpretation

This result means the MVP core request path has been validated against a real upstream service,
not only mocks or unit tests.

It also confirms that token accounting observed reliable usage during the validation run, because
the metrics scrape detected prompt token counters from real traffic.

Taken together with the later production checks, the current compatibility surface
for `POST /v1/chat/completions`, `POST /v1/responses`, and `POST /v1/messages`
has crossed the initial real-environment validation bar.

This validation does not mean the gateway has already been verified for:

- multi-upstream round-robin
- failover behavior
- Docker packaging
- production deployment topology
- timeout or failure injection scenarios
- high-concurrency capacity characterization under the single-process baseline

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
- release-gate, rollback, and runbook validation that now belongs to the launch-stability follow-up work

For current `messages` and `responses` production validation steps, use the
maintenance checklists in
[README.md](README.md).
