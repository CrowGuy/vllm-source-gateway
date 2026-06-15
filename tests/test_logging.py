from __future__ import annotations

import json
import logging

from fastapi import Request

from vllm_source_gateway.logging_utils import JsonLogFormatter, build_access_log_fields
from vllm_source_gateway.request_metrics import RequestMetricsSnapshot


def test_json_log_formatter_emits_json_with_extra_fields() -> None:
    formatter = JsonLogFormatter()
    record = logging.makeLogRecord(
        {
            "name": "vllm_source_gateway",
            "levelname": "INFO",
            "levelno": logging.INFO,
            "msg": "gateway configuration loaded",
            "args": (),
            "config_path": "config.yaml",
            "upstream_count": 2,
        }
    )

    payload = json.loads(formatter.format(record))

    assert payload["logger"] == "vllm_source_gateway"
    assert payload["message"] == "gateway configuration loaded"
    assert payload["config_path"] == "config.yaml"
    assert payload["upstream_count"] == 2
    assert payload["level"] == "INFO"
    assert "timestamp" in payload


def test_build_access_log_fields_includes_request_context() -> None:
    request = Request(
        scope={
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/chat/completions",
            "raw_path": b"/v1/chat/completions",
            "query_string": b"",
            "headers": [
                (b"x-request-id", b"req-123"),
                (b"x-trace-id", b"trace-123"),
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
    )
    snapshot = RequestMetricsSnapshot(
        department="dept-a",
        endpoint="chat_completions",
        method="POST",
        status_code=200,
        duration_seconds=0.1234567,
    )

    fields = build_access_log_fields(request=request, snapshot=snapshot)

    assert fields == {
        "method": "POST",
        "path": "/v1/chat/completions",
        "status_code": 200,
        "duration_seconds": 0.123457,
        "endpoint": "chat_completions",
        "department": "dept-a",
        "request_id": "req-123",
        "trace_id": "trace-123",
    }
