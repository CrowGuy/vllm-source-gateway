from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import Request

from vllm_source_gateway.request_metrics import RequestMetricsSnapshot


_APPLICATION_LOGGER_NAME = "vllm_source_gateway"
_ACCESS_LOGGER_NAME = "vllm_source_gateway.access"
_STANDARD_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__.keys())


def _normalize_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _normalize_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_json_value(item) for item in value]
    return str(value)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key.startswith("_") or key in _STANDARD_LOG_RECORD_FIELDS:
                continue
            payload[key] = _normalize_json_value(value)

        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, separators=(",", ":"))


def configure_application_logging(*, level: int = logging.INFO) -> logging.Logger:
    application_logger = logging.getLogger(_APPLICATION_LOGGER_NAME)

    if getattr(application_logger, "_vllm_source_gateway_configured", False):
        application_logger.setLevel(level)
        return application_logger

    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())

    application_logger.handlers.clear()
    application_logger.addHandler(handler)
    application_logger.setLevel(level)
    application_logger.propagate = False
    application_logger._vllm_source_gateway_configured = True  # type: ignore[attr-defined]

    return application_logger


def build_access_log_fields(
    *,
    request: Request,
    snapshot: RequestMetricsSnapshot,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "method": snapshot.method,
        "path": request.url.path,
        "status_code": snapshot.status_code,
        "duration_seconds": round(snapshot.duration_seconds, 6),
        "endpoint": snapshot.endpoint or request.url.path,
    }

    if snapshot.department is not None:
        fields["department"] = snapshot.department
    if snapshot.failure_origin is not None:
        fields["failure_origin"] = snapshot.failure_origin

    request_id = request.headers.get("x-request-id")
    if request_id is not None:
        fields["request_id"] = request_id

    trace_id = request.headers.get("x-trace-id")
    if trace_id is not None:
        fields["trace_id"] = trace_id

    return fields


def log_request_completion(*, request: Request, snapshot: RequestMetricsSnapshot | None) -> None:
    if snapshot is None:
        return
    if snapshot.endpoint is None and snapshot.status_code < 500:
        return

    access_logger = logging.getLogger(_ACCESS_LOGGER_NAME)
    level = logging.WARNING if snapshot.status_code >= 500 else logging.INFO
    access_logger.log(
        level,
        "request completed",
        extra=build_access_log_fields(request=request, snapshot=snapshot),
    )
