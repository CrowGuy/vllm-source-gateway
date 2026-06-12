#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_PATH="${GATEWAY_CONFIG:-$ROOT_DIR/config.e2e.gemma4.yaml}"
UPSTREAM_BASE_URL="${UPSTREAM_BASE_URL:-http://10.0.0.1:8000}"
GATEWAY_HOST="${GATEWAY_HOST:-127.0.0.1}"
GATEWAY_PORT="${GATEWAY_PORT:-8080}"
MODEL_NAME="${MODEL_NAME:-gemma-4-31b}"
VALIDATION_API_KEY="${VALIDATION_API_KEY:-validation-key}"
UNKNOWN_API_KEY="${UNKNOWN_API_KEY:-unknown-key}"
ARTIFACT_DIR="${ARTIFACT_DIR:-/tmp/vllm-source-gateway-e2e}"
GATEWAY_BASE_URL="http://${GATEWAY_HOST}:${GATEWAY_PORT}"

mkdir -p "$ARTIFACT_DIR"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

require_python_module() {
  local module="$1"
  if ! python3 -c "import ${module}" >/dev/null 2>&1; then
    echo "missing required python module: ${module}" >&2
    exit 1
  fi
}

require_command curl
require_command python3
require_python_module uvicorn
require_python_module fastapi
require_python_module httpx
require_python_module yaml
require_python_module prometheus_client

cleanup() {
  if [[ -n "${GATEWAY_PID:-}" ]] && kill -0 "$GATEWAY_PID" >/dev/null 2>&1; then
    kill "$GATEWAY_PID" >/dev/null 2>&1 || true
    wait "$GATEWAY_PID" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT

run_json_post() {
  local url="$1"
  local body="$2"
  local output="$3"

  local status
  status="$(curl -sS -o "$output" -w '%{http_code}' \
    -H 'content-type: application/json' \
    -X POST "$url" \
    --data "$body")"
  echo "$status"
}

wait_for_gateway() {
  local attempts=30
  local count=0

  until curl -sS "${GATEWAY_BASE_URL}/healthz" >/dev/null 2>&1; do
    count=$((count + 1))
    if [[ "$count" -ge "$attempts" ]]; then
      echo "gateway did not become ready at ${GATEWAY_BASE_URL}" >&2
      exit 1
    fi
    sleep 1
  done
}

assert_json_contains_model() {
  local file="$1"
  python3 - "$file" "$MODEL_NAME" <<'PY'
import json
import sys

payload = json.loads(open(sys.argv[1], encoding="utf-8").read())
model_name = sys.argv[2]

models = payload.get("data", [])
if not any(item.get("id") == model_name for item in models):
    raise SystemExit(f"model {model_name!r} not found in /v1/models response")
PY
}

assert_metrics_contains() {
  local pattern="$1"
  local metrics_file="$2"
  if ! grep -F "$pattern" "$metrics_file" >/dev/null 2>&1; then
    echo "expected metrics pattern not found: $pattern" >&2
    exit 1
  fi
}

echo "[1/9] upstream capability smoke check"
CHAT_BODY="$(cat <<EOF
{"model":"${MODEL_NAME}","messages":[{"role":"user","content":"Reply with exactly: gateway smoke check"}]}
EOF
)"
RESPONSES_BODY="$(cat <<EOF
{"model":"${MODEL_NAME}","input":"Reply with exactly: gateway responses smoke check"}
EOF
)"

UPSTREAM_CHAT_STATUS="$(run_json_post "${UPSTREAM_BASE_URL}/v1/chat/completions" "$CHAT_BODY" "$ARTIFACT_DIR/upstream_chat.json")"
echo "upstream /v1/chat/completions -> ${UPSTREAM_CHAT_STATUS}"
if [[ "$UPSTREAM_CHAT_STATUS" != "200" ]]; then
  echo "upstream chat smoke check failed" >&2
  exit 1
fi

UPSTREAM_RESPONSES_STATUS="$(run_json_post "${UPSTREAM_BASE_URL}/v1/responses" "$RESPONSES_BODY" "$ARTIFACT_DIR/upstream_responses.json")"
echo "upstream /v1/responses -> ${UPSTREAM_RESPONSES_STATUS}"
if [[ "$UPSTREAM_RESPONSES_STATUS" != "200" ]]; then
  echo "upstream responses smoke check failed" >&2
  exit 1
fi

echo "[2/9] gateway startup and health"
export PYTHONPATH="$ROOT_DIR/src:${PYTHONPATH:-}"
export VLLM_SOURCE_GATEWAY_CONFIG="$CONFIG_PATH"
python3 -m uvicorn vllm_source_gateway.main:app --host 0.0.0.0 --port "$GATEWAY_PORT" \
  >"$ARTIFACT_DIR/gateway.log" 2>&1 &
GATEWAY_PID="$!"
wait_for_gateway
curl -sS "${GATEWAY_BASE_URL}/healthz" >"$ARTIFACT_DIR/healthz.json"

echo "[3/9] model discovery"
curl -sS "${GATEWAY_BASE_URL}/v1/models" >"$ARTIFACT_DIR/models.json"
assert_json_contains_model "$ARTIFACT_DIR/models.json"

echo "[4/9] non-streaming chat"
CHAT_GATEWAY_STATUS="$(curl -sS -o "$ARTIFACT_DIR/gateway_chat.json" -w '%{http_code}' \
  -H 'content-type: application/json' \
  -H "x-api-key: ${VALIDATION_API_KEY}" \
  -X POST "${GATEWAY_BASE_URL}/v1/chat/completions" \
  --data "$CHAT_BODY")"
echo "gateway /v1/chat/completions -> ${CHAT_GATEWAY_STATUS}"
if [[ "$CHAT_GATEWAY_STATUS" != "200" ]]; then
  echo "gateway chat validation failed" >&2
  exit 1
fi

echo "[5/9] non-streaming responses"
RESPONSES_GATEWAY_STATUS="$(curl -sS -o "$ARTIFACT_DIR/gateway_responses.json" -w '%{http_code}' \
  -H 'content-type: application/json' \
  -H "x-api-key: ${VALIDATION_API_KEY}" \
  -X POST "${GATEWAY_BASE_URL}/v1/responses" \
  --data "$RESPONSES_BODY")"
echo "gateway /v1/responses -> ${RESPONSES_GATEWAY_STATUS}"
if [[ "$RESPONSES_GATEWAY_STATUS" != "200" ]]; then
  echo "gateway responses validation failed" >&2
  exit 1
fi

echo "[6/9] metrics scrape and label checks"
curl -sS "${GATEWAY_BASE_URL}/metrics" >"$ARTIFACT_DIR/metrics_after_non_streaming.prom"
assert_metrics_contains 'gateway_http_requests_total{department="validation",endpoint="chat_completions",method="POST",status_class="2xx"}' "$ARTIFACT_DIR/metrics_after_non_streaming.prom"
assert_metrics_contains 'gateway_http_requests_total{department="validation",endpoint="responses",method="POST",status_class="2xx"}' "$ARTIFACT_DIR/metrics_after_non_streaming.prom"
assert_metrics_contains 'gateway_source_resolution_total{department="validation",resolution_source="api_key"}' "$ARTIFACT_DIR/metrics_after_non_streaming.prom"
if grep -F 'gateway_prompt_tokens_total{department="validation"' "$ARTIFACT_DIR/metrics_after_non_streaming.prom" >/dev/null 2>&1; then
  echo "reliable prompt token metrics detected"
else
  assert_metrics_contains 'gateway_token_accounting_total{accounting_status="missing_usage",endpoint="chat_completions"}' "$ARTIFACT_DIR/metrics_after_non_streaming.prom"
fi

echo "[7/9] streaming chat"
curl -sS -N -o "$ARTIFACT_DIR/gateway_chat_stream.txt" \
  -H 'content-type: application/json' \
  -H "x-api-key: ${VALIDATION_API_KEY}" \
  -X POST "${GATEWAY_BASE_URL}/v1/chat/completions" \
  --data "{\"model\":\"${MODEL_NAME}\",\"messages\":[{\"role\":\"user\",\"content\":\"Count to 3 in one short line\"}],\"stream\":true}"
grep -F 'data:' "$ARTIFACT_DIR/gateway_chat_stream.txt" >/dev/null 2>&1 || {
  echo "chat streaming output did not contain SSE data lines" >&2
  exit 1
}

echo "[8/9] streaming responses"
curl -sS -N -o "$ARTIFACT_DIR/gateway_responses_stream.txt" \
  -H 'content-type: application/json' \
  -H "x-api-key: ${VALIDATION_API_KEY}" \
  -X POST "${GATEWAY_BASE_URL}/v1/responses" \
  --data "{\"model\":\"${MODEL_NAME}\",\"input\":\"Count to 3 in one short line\",\"stream\":true}"
grep -F 'data:' "$ARTIFACT_DIR/gateway_responses_stream.txt" >/dev/null 2>&1 || {
  echo "responses streaming output did not contain SSE data lines" >&2
  exit 1
}

echo "[9/9] unknown department behavior"
UNKNOWN_STATUS="$(curl -sS -o "$ARTIFACT_DIR/gateway_unknown_chat.json" -w '%{http_code}' \
  -H 'content-type: application/json' \
  -H "x-api-key: ${UNKNOWN_API_KEY}" \
  -X POST "${GATEWAY_BASE_URL}/v1/chat/completions" \
  --data "$CHAT_BODY")"
echo "gateway unknown department chat -> ${UNKNOWN_STATUS}"
if [[ "$UNKNOWN_STATUS" != "200" ]]; then
  echo "unknown department validation failed" >&2
  exit 1
fi

curl -sS "${GATEWAY_BASE_URL}/metrics" >"$ARTIFACT_DIR/metrics_final.prom"
assert_metrics_contains 'gateway_source_resolution_total{department="unknown",resolution_source="unknown"}' "$ARTIFACT_DIR/metrics_final.prom"

echo
echo "E2E validation completed successfully."
echo "Artifacts:"
echo "  $ARTIFACT_DIR"
