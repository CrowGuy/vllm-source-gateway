from __future__ import annotations

import time

from fastapi import Request

from vllm_source_gateway.metrics import GatewayMetrics


_REQUEST_STARTED_AT = "_gateway_request_metrics_started_at"
_REQUEST_RECORDED = "_gateway_request_metrics_recorded"
_REQUEST_ENDPOINT = "_gateway_request_metrics_endpoint"
_REQUEST_DEPARTMENT = "_gateway_request_metrics_department"
_REQUEST_STATUS_CODE_OVERRIDE = "_gateway_request_metrics_status_code_override"


def initialize_request_metrics_state(request: Request) -> None:
    request.state.__setattr__(_REQUEST_STARTED_AT, time.perf_counter())
    request.state.__setattr__(_REQUEST_RECORDED, False)
    request.state.__setattr__(_REQUEST_ENDPOINT, None)
    request.state.__setattr__(_REQUEST_DEPARTMENT, None)
    request.state.__setattr__(_REQUEST_STATUS_CODE_OVERRIDE, None)


def set_request_metrics_context(request: Request, *, department: str, endpoint: str) -> None:
    request.state.__setattr__(_REQUEST_DEPARTMENT, department)
    request.state.__setattr__(_REQUEST_ENDPOINT, endpoint)


def set_request_metrics_status_override(request: Request, *, status_code: int) -> None:
    request.state.__setattr__(_REQUEST_STATUS_CODE_OVERRIDE, status_code)


def finalize_request_metrics(request: Request, *, default_status_code: int) -> None:
    if getattr(request.state, _REQUEST_RECORDED, False):
        return

    started_at = getattr(request.state, _REQUEST_STARTED_AT, None)
    department = getattr(request.state, _REQUEST_DEPARTMENT, None)
    endpoint = getattr(request.state, _REQUEST_ENDPOINT, None)
    if started_at is None or department is None or endpoint is None:
        return

    metrics: GatewayMetrics = request.app.state.metrics
    status_code_override = getattr(request.state, _REQUEST_STATUS_CODE_OVERRIDE, None)
    status_code = status_code_override if status_code_override is not None else default_status_code

    metrics.observe_request(
        department=department,
        endpoint=endpoint,
        method=request.method,
        status_code=status_code,
        duration_seconds=time.perf_counter() - started_at,
    )
    request.state.__setattr__(_REQUEST_RECORDED, True)
