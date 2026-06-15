from __future__ import annotations

import time
from dataclasses import dataclass

from fastapi import Request

from vllm_source_gateway.metrics import GatewayMetrics


_REQUEST_STARTED_AT = "_gateway_request_metrics_started_at"
_REQUEST_RECORDED = "_gateway_request_metrics_recorded"
_REQUEST_ENDPOINT = "_gateway_request_metrics_endpoint"
_REQUEST_DEPARTMENT = "_gateway_request_metrics_department"
_REQUEST_STATUS_CODE_OVERRIDE = "_gateway_request_metrics_status_code_override"
_REQUEST_FAILURE_ORIGIN = "_gateway_request_metrics_failure_origin"


@dataclass(frozen=True)
class RequestMetricsSnapshot:
    department: str | None
    endpoint: str | None
    method: str
    status_code: int
    duration_seconds: float
    failure_origin: str | None


def initialize_request_metrics_state(request: Request) -> None:
    request.state.__setattr__(_REQUEST_STARTED_AT, time.perf_counter())
    request.state.__setattr__(_REQUEST_RECORDED, False)
    request.state.__setattr__(_REQUEST_ENDPOINT, None)
    request.state.__setattr__(_REQUEST_DEPARTMENT, None)
    request.state.__setattr__(_REQUEST_STATUS_CODE_OVERRIDE, None)
    request.state.__setattr__(_REQUEST_FAILURE_ORIGIN, None)


def set_request_metrics_context(request: Request, *, department: str, endpoint: str) -> None:
    request.state.__setattr__(_REQUEST_DEPARTMENT, department)
    request.state.__setattr__(_REQUEST_ENDPOINT, endpoint)


def set_request_metrics_status_override(request: Request, *, status_code: int) -> None:
    request.state.__setattr__(_REQUEST_STATUS_CODE_OVERRIDE, status_code)


def set_request_metrics_failure_origin(request: Request, *, failure_origin: str) -> None:
    request.state.__setattr__(_REQUEST_FAILURE_ORIGIN, failure_origin)


def build_request_metrics_snapshot(
    request: Request,
    *,
    default_status_code: int,
) -> RequestMetricsSnapshot | None:
    started_at = getattr(request.state, _REQUEST_STARTED_AT, None)
    if started_at is None:
        return None

    department = getattr(request.state, _REQUEST_DEPARTMENT, None)
    endpoint = getattr(request.state, _REQUEST_ENDPOINT, None)
    status_code_override = getattr(request.state, _REQUEST_STATUS_CODE_OVERRIDE, None)
    status_code = status_code_override if status_code_override is not None else default_status_code
    failure_origin = getattr(request.state, _REQUEST_FAILURE_ORIGIN, None)
    if failure_origin is None and status_code >= 400:
        failure_origin = "gateway"

    return RequestMetricsSnapshot(
        department=department,
        endpoint=endpoint,
        method=request.method,
        status_code=status_code,
        duration_seconds=time.perf_counter() - started_at,
        failure_origin=failure_origin,
    )


def finalize_request_metrics(
    request: Request,
    *,
    default_status_code: int,
) -> RequestMetricsSnapshot | None:
    if getattr(request.state, _REQUEST_RECORDED, False):
        return None

    snapshot = build_request_metrics_snapshot(request, default_status_code=default_status_code)
    if snapshot is None:
        return None

    if snapshot.department is not None and snapshot.endpoint is not None:
        metrics: GatewayMetrics = request.app.state.metrics
        metrics.observe_request(
            department=snapshot.department,
            endpoint=snapshot.endpoint,
            method=snapshot.method,
            status_code=snapshot.status_code,
            duration_seconds=snapshot.duration_seconds,
        )
        if snapshot.failure_origin is not None:
            metrics.record_request_failure(
                department=snapshot.department,
                endpoint=snapshot.endpoint,
                method=snapshot.method,
                status_code=snapshot.status_code,
                failure_origin=snapshot.failure_origin,
            )

    request.state.__setattr__(_REQUEST_RECORDED, True)
    return snapshot
