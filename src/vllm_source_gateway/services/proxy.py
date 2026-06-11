from __future__ import annotations

import time
from typing import Any, Callable

import httpx
from fastapi import HTTPException, Request, Response, status

from vllm_source_gateway.config import AppConfig
from vllm_source_gateway.dependencies import SourceResolutionResult
from vllm_source_gateway.metrics import GatewayMetrics
from vllm_source_gateway.routing import NoHealthyUpstreamError, RoutingRegistry, UnknownModelError

UsageExtractor = Callable[[dict[str, Any]], tuple[int, int] | None]

_EXCLUDED_UPSTREAM_HEADERS = {"authorization", "content-length", "host", "x-api-key"}


def _build_upstream_headers(request: Request) -> dict[str, str]:
    return {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _EXCLUDED_UPSTREAM_HEADERS
    }


def _record_request(
    *,
    metrics: GatewayMetrics,
    department: str,
    endpoint: str,
    method: str,
    status_code: int,
    started_at: float,
) -> None:
    metrics.observe_request(
        department=department,
        endpoint=endpoint,
        method=method,
        status_code=status_code,
        duration_seconds=time.perf_counter() - started_at,
    )


def _raise_http_error(
    *,
    metrics: GatewayMetrics,
    department: str,
    endpoint: str,
    method: str,
    status_code: int,
    started_at: float,
    detail: str,
    accounting_status: str | None = None,
) -> HTTPException:
    if accounting_status is not None:
        metrics.record_token_accounting(endpoint=endpoint, accounting_status=accounting_status)

    _record_request(
        metrics=metrics,
        department=department,
        endpoint=endpoint,
        method=method,
        status_code=status_code,
        started_at=started_at,
    )
    return HTTPException(status_code=status_code, detail=detail)


async def proxy_json_endpoint(
    *,
    request: Request,
    config: AppConfig,
    routing_registry: RoutingRegistry,
    metrics: GatewayMetrics,
    source_resolution: SourceResolutionResult,
    endpoint_name: str,
    upstream_path: str,
    usage_extractor: UsageExtractor,
) -> Response:
    started_at = time.perf_counter()
    method = request.method
    department = source_resolution.department

    metrics.record_source_resolution(
        department=department,
        resolution_source=source_resolution.resolution_source,
    )

    try:
        payload = await request.json()
    except Exception as exc:  # pragma: no cover - FastAPI request parsing edge
        raise _raise_http_error(
            metrics=metrics,
            department=department,
            endpoint=endpoint_name,
            method=method,
            status_code=status.HTTP_400_BAD_REQUEST,
            started_at=started_at,
            detail="invalid json body",
        ) from exc

    if not isinstance(payload, dict):
        raise _raise_http_error(
            metrics=metrics,
            department=department,
            endpoint=endpoint_name,
            method=method,
            status_code=status.HTTP_400_BAD_REQUEST,
            started_at=started_at,
            detail="request body must be a json object",
        )

    model_name = payload.get("model")
    if not isinstance(model_name, str) or not model_name.strip():
        raise _raise_http_error(
            metrics=metrics,
            department=department,
            endpoint=endpoint_name,
            method=method,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            started_at=started_at,
            detail="missing model",
        )

    if payload.get("stream") is True:
        raise _raise_http_error(
            metrics=metrics,
            department=department,
            endpoint=endpoint_name,
            method=method,
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            started_at=started_at,
            detail="streaming not implemented yet",
            accounting_status="missing_usage",
        )

    try:
        selected = routing_registry.select_upstream(model_name)
    except UnknownModelError as exc:
        raise _raise_http_error(
            metrics=metrics,
            department=department,
            endpoint=endpoint_name,
            method=method,
            status_code=status.HTTP_404_NOT_FOUND,
            started_at=started_at,
            detail=str(exc),
        ) from exc
    except NoHealthyUpstreamError as exc:
        raise _raise_http_error(
            metrics=metrics,
            department=department,
            endpoint=endpoint_name,
            method=method,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            started_at=started_at,
            detail=str(exc),
        ) from exc

    timeout = httpx.Timeout(
        timeout=config.timeouts.upstream_request_seconds,
        connect=config.timeouts.connect_seconds,
    )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            upstream_response = await client.post(
                f"{selected.upstream.base_url}{upstream_path}",
                json=payload,
                headers=_build_upstream_headers(request),
            )
    except httpx.TimeoutException as exc:
        raise _raise_http_error(
            metrics=metrics,
            department=department,
            endpoint=endpoint_name,
            method=method,
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            started_at=started_at,
            detail="upstream request timed out",
            accounting_status="missing_usage",
        ) from exc
    except httpx.HTTPError as exc:
        raise _raise_http_error(
            metrics=metrics,
            department=department,
            endpoint=endpoint_name,
            method=method,
            status_code=status.HTTP_502_BAD_GATEWAY,
            started_at=started_at,
            detail="upstream request failed",
            accounting_status="missing_usage",
        ) from exc

    try:
        response_payload = upstream_response.json()
    except ValueError:
        metrics.record_token_accounting(endpoint=endpoint_name, accounting_status="parse_error")
        _record_request(
            metrics=metrics,
            department=department,
            endpoint=endpoint_name,
            method=method,
            status_code=upstream_response.status_code,
            started_at=started_at,
        )
        return Response(
            content=upstream_response.content,
            status_code=upstream_response.status_code,
            media_type=upstream_response.headers.get("content-type"),
        )

    usage = usage_extractor(response_payload)
    if usage is None:
        metrics.record_token_accounting(endpoint=endpoint_name, accounting_status="missing_usage")
    else:
        prompt_tokens, generation_tokens = usage
        metrics.record_prompt_tokens(
            department=department,
            model_name=model_name,
            prompt_tokens=prompt_tokens,
        )
        metrics.record_generation_tokens(
            department=department,
            model_name=model_name,
            generation_tokens=generation_tokens,
        )
        metrics.record_token_accounting(endpoint=endpoint_name, accounting_status="recorded")

    _record_request(
        metrics=metrics,
        department=department,
        endpoint=endpoint_name,
        method=method,
        status_code=upstream_response.status_code,
        started_at=started_at,
    )

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        media_type=upstream_response.headers.get("content-type", "application/json"),
    )
