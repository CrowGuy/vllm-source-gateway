from __future__ import annotations

import asyncio
import codecs
import json
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from vllm_source_gateway.config import AppConfig
from vllm_source_gateway.dependencies import SourceResolutionResult
from vllm_source_gateway.metrics import GatewayMetrics
from vllm_source_gateway.request_metrics import (
    set_request_metrics_context,
    set_request_metrics_failure_origin,
    set_request_metrics_status_override,
)
from vllm_source_gateway.routing import NoHealthyUpstreamError, RoutingRegistry, UnknownModelError
from vllm_source_gateway.services.admission_control import AdmissionController, AdmissionLease

UsageExtractor = Callable[[dict[str, Any]], tuple[int, int] | None]
logger = logging.getLogger("vllm_source_gateway.proxy")

_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_ALLOWED_UPSTREAM_HEADERS = {
    "accept",
    "accept-language",
    "content-type",
    "user-agent",
    "openai-organization",
    "openai-project",
    "openai-beta",
    "idempotency-key",
    "traceparent",
    "tracestate",
    "baggage",
    "x-request-id",
    "x-correlation-id",
    "x-trace-id",
    "x-openai-client-user-agent",
}
_ALLOWED_UPSTREAM_HEADER_PREFIXES = ("x-stainless-",)
_BLOCKED_UPSTREAM_HEADERS = _HOP_BY_HOP_HEADERS | {
    "authorization",
    "accept-encoding",
    "content-length",
    "cookie",
    "host",
    "x-api-key",
}
_EXCLUDED_DOWNSTREAM_HEADERS = _HOP_BY_HOP_HEADERS | {
    "content-encoding",
    "content-length",
}
_CLIENT_DISCONNECTED_STATUS = 499


def _should_forward_upstream_header(header_name: str) -> bool:
    normalized = header_name.lower()
    if normalized in _BLOCKED_UPSTREAM_HEADERS:
        return False
    if normalized in _ALLOWED_UPSTREAM_HEADERS:
        return True
    return normalized.startswith(_ALLOWED_UPSTREAM_HEADER_PREFIXES)


def _build_upstream_headers(
    request: Request,
    *,
    authorization_token: str | None = None,
) -> dict[str, str]:
    headers = {
        key: value
        for key, value in request.headers.items()
        if _should_forward_upstream_header(key)
    }
    if authorization_token is not None:
        headers["authorization"] = f"Bearer {authorization_token}"
    return headers


def _record_usage(
    *,
    metrics: GatewayMetrics,
    department: str,
    endpoint_name: str,
    model_name: str,
    upstream_status_code: int,
    usage: tuple[int, int] | None,
) -> tuple[int, int] | None:
    if not 200 <= upstream_status_code < 300:
        metrics.record_token_accounting(endpoint=endpoint_name, accounting_status="missing_usage")
        return None

    if usage is None:
        metrics.record_token_accounting(endpoint=endpoint_name, accounting_status="missing_usage")
        return None

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
    return usage


def _raise_http_error(
    *,
    metrics: GatewayMetrics,
    endpoint: str,
    status_code: int,
    detail: str,
    accounting_status: str | None = None,
) -> HTTPException:
    if accounting_status is not None:
        metrics.record_token_accounting(endpoint=endpoint, accounting_status=accounting_status)
    return HTTPException(status_code=status_code, detail=detail)


def _build_downstream_headers(upstream_headers: httpx.Headers) -> dict[str, str]:
    return {
        key: value
        for key, value in upstream_headers.items()
        if key.lower() not in _EXCLUDED_DOWNSTREAM_HEADERS
    }


def _request_body_too_large_error(
    *,
    metrics: GatewayMetrics,
    endpoint_name: str,
    max_request_body_bytes: int,
) -> HTTPException:
    return _raise_http_error(
        metrics=metrics,
        endpoint=endpoint_name,
        status_code=413,
        detail=(
            "request body too large; "
            f"max_request_body_bytes={max_request_body_bytes}"
        ),
    )


def _content_length_exceeds_limit(request: Request, max_request_body_bytes: int) -> bool:
    content_length = request.headers.get("content-length")
    if content_length is None:
        return False

    try:
        parsed_content_length = int(content_length)
    except ValueError:
        return False

    return parsed_content_length > max_request_body_bytes


async def _read_json_request_body(
    *,
    request: Request,
    config: AppConfig,
    metrics: GatewayMetrics,
    endpoint_name: str,
) -> tuple[Any, int]:
    max_request_body_bytes = config.server.max_request_body_bytes

    if _content_length_exceeds_limit(request, max_request_body_bytes):
        raise _request_body_too_large_error(
            metrics=metrics,
            endpoint_name=endpoint_name,
            max_request_body_bytes=max_request_body_bytes,
        )

    try:
        request_body = await request.body()
    except Exception as exc:  # pragma: no cover - FastAPI request parsing edge
        raise _raise_http_error(
            metrics=metrics,
            endpoint=endpoint_name,
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid request body",
        ) from exc

    if len(request_body) > max_request_body_bytes:
        raise _request_body_too_large_error(
            metrics=metrics,
            endpoint_name=endpoint_name,
            max_request_body_bytes=max_request_body_bytes,
        )

    try:
        return json.loads(request_body), len(request_body)
    except ValueError as exc:
        raise _raise_http_error(
            metrics=metrics,
            endpoint=endpoint_name,
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid json body",
        ) from exc


def _parse_json_payload(
    *,
    payload: Any,
    metrics: GatewayMetrics,
    endpoint_name: str,
) -> tuple[dict[str, Any], str]:
    if not isinstance(payload, dict):
        raise _raise_http_error(
            metrics=metrics,
            endpoint=endpoint_name,
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="request body must be a json object",
        )

    model_name = payload.get("model")
    if not isinstance(model_name, str) or not model_name.strip():
        raise _raise_http_error(
            metrics=metrics,
            endpoint=endpoint_name,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="missing model",
        )

    return payload, model_name


def _enforce_source_resolution_policy(
    *,
    config: AppConfig,
    source_resolution: SourceResolutionResult,
    metrics: GatewayMetrics,
    endpoint_name: str,
) -> None:
    if not config.security.reject_unknown_api_keys:
        return

    if source_resolution.resolution_source != "unknown":
        return

    detail = "missing api key" if source_resolution.api_key is None else "unknown api key"
    raise _raise_http_error(
        metrics=metrics,
        endpoint=endpoint_name,
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
    )


def _select_upstream(
    *,
    routing_registry: RoutingRegistry,
    model_name: str,
    metrics: GatewayMetrics,
    endpoint_name: str,
    excluded_upstream_names: set[str] | None = None,
) -> tuple[str, str | None, str]:
    try:
        selected = routing_registry.select_upstream(
            model_name,
            excluded_upstream_names=excluded_upstream_names,
        )
    except UnknownModelError as exc:
        raise _raise_http_error(
            metrics=metrics,
            endpoint=endpoint_name,
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except NoHealthyUpstreamError as exc:
        raise _raise_http_error(
            metrics=metrics,
            endpoint=endpoint_name,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    metrics.record_upstream_selection(
        model_name=model_name,
        upstream_name=selected.upstream.name,
    )

    return selected.upstream.base_url, selected.upstream.authorization_token, selected.upstream.name


def _ensure_known_model(
    *,
    routing_registry: RoutingRegistry,
    model_name: str,
    metrics: GatewayMetrics,
    endpoint_name: str,
) -> None:
    try:
        routing_registry.get_model_upstreams(model_name)
    except UnknownModelError as exc:
        raise _raise_http_error(
            metrics=metrics,
            endpoint=endpoint_name,
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


def _is_connect_stage_retryable_error(exc: Exception) -> bool:
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))


def _raise_upstream_transport_error(
    *,
    metrics: GatewayMetrics,
    endpoint_name: str,
    exc: Exception,
    admission_controller: AdmissionController | None = None,
    department: str | None = None,
    model_name: str | None = None,
) -> HTTPException:
    if admission_controller is not None and department is not None and model_name is not None:
        admission_controller.record_retry_event(department=department, model_name=model_name)

    if isinstance(exc, httpx.TimeoutException):
        raise _raise_http_error(
            metrics=metrics,
            endpoint=endpoint_name,
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="upstream request timed out",
            accounting_status="missing_usage",
        ) from exc

    raise _raise_http_error(
        metrics=metrics,
        endpoint=endpoint_name,
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="upstream request failed",
        accounting_status="missing_usage",
    ) from exc


def _consume_sse_events(
    *,
    buffer: str,
    fragment: str,
    max_buffer_bytes: int,
    usage_extractor: UsageExtractor,
    latest_usage: tuple[int, int] | None,
) -> tuple[str, tuple[int, int] | None, bool]:
    normalized_buffer = (buffer + fragment).replace("\r\n", "\n")

    if len(normalized_buffer.encode("utf-8")) > max_buffer_bytes:
        return "", latest_usage, True

    while "\n\n" in normalized_buffer:
        raw_event, normalized_buffer = normalized_buffer.split("\n\n", 1)
        data_lines = []
        for line in raw_event.split("\n"):
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())

        if not data_lines:
            continue

        event_payload = "\n".join(data_lines)
        if event_payload == "[DONE]":
            continue

        try:
            parsed_payload = json.loads(event_payload)
        except json.JSONDecodeError:
            continue

        usage = usage_extractor(parsed_payload)
        if usage is not None:
            latest_usage = usage

    return normalized_buffer, latest_usage, False


def _request_timeout(config: AppConfig) -> httpx.Timeout:
    return httpx.Timeout(
        timeout=config.timeouts.upstream_request_seconds,
        connect=config.timeouts.connect_seconds,
    )


async def _proxy_streaming_response(
    *,
    request: Request,
    config: AppConfig,
    routing_registry: RoutingRegistry,
    metrics: GatewayMetrics,
    upstream_streaming_http_client: httpx.AsyncClient,
    department: str,
    endpoint_name: str,
    upstream_base_url: str,
    upstream_path: str,
    upstream_name: str,
    upstream_authorization_token: str | None,
    payload: dict[str, Any],
    model_name: str,
    usage_extractor: UsageExtractor,
    admission_controller: AdmissionController,
    admission_lease: AdmissionLease | None,
) -> Response:
    attempted_upstream_names: set[str] = {upstream_name}
    max_sse_decode_buffer_bytes = config.server.max_sse_decode_buffer_bytes

    while True:
        upstream_first_chunk_started_at: float | None = None
        request_headers = _build_upstream_headers(
            request,
            authorization_token=upstream_authorization_token,
        )

        try:
            upstream_request = upstream_streaming_http_client.build_request(
                "POST",
                f"{upstream_base_url}{upstream_path}",
                json=payload,
                headers=request_headers,
            )
            upstream_response = await upstream_streaming_http_client.send(
                upstream_request,
                stream=True,
            )
            upstream_first_chunk_started_at = time.perf_counter()
            break
        except httpx.HTTPError as exc:
            if _is_connect_stage_retryable_error(exc):
                try:
                    (
                        upstream_base_url,
                        upstream_authorization_token,
                        upstream_name,
                    ) = _select_upstream(
                        routing_registry=routing_registry,
                        model_name=model_name,
                        metrics=metrics,
                        endpoint_name=endpoint_name,
                        excluded_upstream_names=attempted_upstream_names,
                    )
                except HTTPException:
                    _raise_upstream_transport_error(
                        metrics=metrics,
                        endpoint_name=endpoint_name,
                        exc=exc,
                        admission_controller=admission_controller,
                        department=department,
                        model_name=model_name,
                    )
                attempted_upstream_names.add(upstream_name)
                continue

            _raise_upstream_transport_error(
                metrics=metrics,
                endpoint_name=endpoint_name,
                exc=exc,
                admission_controller=admission_controller,
                department=department,
                model_name=model_name,
            )

    if upstream_response.status_code >= 400:
        set_request_metrics_failure_origin(request, failure_origin="upstream")
    if upstream_response.status_code >= 500:
        admission_controller.record_retry_event(department=department, model_name=model_name)

    async def _stream_bytes():
        latest_usage: tuple[int, int] | None = None
        decode_buffer = ""
        parsing_enabled = True
        accounting_status_override: str | None = None
        decoder = codecs.getincrementaldecoder("utf-8")()
        client_disconnected = False
        stream_failed = False
        first_chunk_recorded = False

        try:
            async for chunk in upstream_response.aiter_bytes():
                if await request.is_disconnected():
                    client_disconnected = True
                    break

                if (
                    chunk
                    and not first_chunk_recorded
                    and upstream_first_chunk_started_at is not None
                ):
                    metrics.observe_stream_first_chunk(
                        department=department,
                        model_name=model_name,
                        endpoint=endpoint_name,
                        duration_seconds=time.perf_counter() - upstream_first_chunk_started_at,
                    )
                    first_chunk_recorded = True

                if parsing_enabled and chunk:
                    try:
                        fragment = decoder.decode(chunk)
                    except UnicodeDecodeError:
                        parsing_enabled = False
                        accounting_status_override = "parse_error"
                    else:
                        decode_buffer, latest_usage, buffer_overflowed = _consume_sse_events(
                            buffer=decode_buffer,
                            fragment=fragment,
                            max_buffer_bytes=max_sse_decode_buffer_bytes,
                            usage_extractor=usage_extractor,
                            latest_usage=latest_usage,
                        )
                        if buffer_overflowed:
                            parsing_enabled = False
                            decode_buffer = ""
                            accounting_status_override = "parse_error"
                            logger.warning(
                                "disabled stream usage parsing after SSE decode buffer exceeded "
                                "limit",
                                extra={
                                    "endpoint": endpoint_name,
                                    "model_name": model_name,
                                    "max_sse_decode_buffer_bytes": max_sse_decode_buffer_bytes,
                                    "upstream_name": upstream_name,
                                },
                            )

                yield chunk

            if parsing_enabled and not client_disconnected:
                try:
                    final_fragment = decoder.decode(b"", final=True)
                except UnicodeDecodeError:
                    parsing_enabled = False
                    accounting_status_override = "parse_error"
                else:
                    decode_buffer, latest_usage, buffer_overflowed = _consume_sse_events(
                        buffer=decode_buffer,
                        fragment=final_fragment,
                        max_buffer_bytes=max_sse_decode_buffer_bytes,
                        usage_extractor=usage_extractor,
                        latest_usage=latest_usage,
                    )
                    if buffer_overflowed:
                        accounting_status_override = "parse_error"
        except asyncio.CancelledError:
            client_disconnected = True
            raise
        except httpx.HTTPError:
            stream_failed = True
            raise
        finally:
            await upstream_response.aclose()
            admission_controller.release(admission_lease)

            if client_disconnected:
                set_request_metrics_status_override(
                    request,
                    status_code=_CLIENT_DISCONNECTED_STATUS,
                )
                set_request_metrics_failure_origin(request, failure_origin="gateway")
                metrics.record_token_accounting(
                    endpoint=endpoint_name,
                    accounting_status="missing_usage",
                )
                return

            if stream_failed:
                set_request_metrics_status_override(
                    request,
                    status_code=status.HTTP_502_BAD_GATEWAY,
                )
                set_request_metrics_failure_origin(request, failure_origin="gateway")
                metrics.record_token_accounting(
                    endpoint=endpoint_name,
                    accounting_status="missing_usage",
                )
                return

            if accounting_status_override is not None:
                metrics.record_token_accounting(
                    endpoint=endpoint_name,
                    accounting_status=accounting_status_override,
                )
                return

            recorded_usage = _record_usage(
                metrics=metrics,
                department=department,
                endpoint_name=endpoint_name,
                model_name=model_name,
                upstream_status_code=upstream_response.status_code,
                usage=latest_usage,
            )
            if recorded_usage is not None:
                admission_controller.record_tokens(
                    department=department,
                    model_name=model_name,
                    tokens=sum(recorded_usage),
                )

    return StreamingResponse(
        _stream_bytes(),
        status_code=upstream_response.status_code,
        headers=_build_downstream_headers(upstream_response.headers),
    )


async def proxy_json_endpoint(
    *,
    request: Request,
    config: AppConfig,
    routing_registry: RoutingRegistry,
    metrics: GatewayMetrics,
    admission_controller: AdmissionController,
    source_resolution: SourceResolutionResult,
    upstream_http_client: httpx.AsyncClient,
    upstream_streaming_http_client: httpx.AsyncClient,
    endpoint_name: str,
    upstream_path: str,
    usage_extractor: UsageExtractor,
) -> Response:
    department = source_resolution.department
    set_request_metrics_context(
        request,
        department=department,
        endpoint=endpoint_name,
    )

    metrics.record_source_resolution(
        department=department,
        resolution_source=source_resolution.resolution_source,
    )
    _enforce_source_resolution_policy(
        config=config,
        source_resolution=source_resolution,
        metrics=metrics,
        endpoint_name=endpoint_name,
    )

    payload, body_size = await _read_json_request_body(
        request=request,
        config=config,
        metrics=metrics,
        endpoint_name=endpoint_name,
    )

    payload, model_name = _parse_json_payload(
        payload=payload,
        metrics=metrics,
        endpoint_name=endpoint_name,
    )
    _ensure_known_model(
        routing_registry=routing_registry,
        model_name=model_name,
        metrics=metrics,
        endpoint_name=endpoint_name,
    )
    payload = admission_controller.check_request_shape(
        department=department,
        model_name=model_name,
        endpoint=endpoint_name,
        body_size=body_size,
        payload=payload,
    )
    admission_lease = admission_controller.acquire(
        department=department,
        model_name=model_name,
        endpoint=endpoint_name,
    )

    if payload.get("stream") is True:
        try:
            upstream_base_url, upstream_authorization_token, upstream_name = _select_upstream(
                routing_registry=routing_registry,
                model_name=model_name,
                metrics=metrics,
                endpoint_name=endpoint_name,
            )
            return await _proxy_streaming_response(
                request=request,
                config=config,
                routing_registry=routing_registry,
                metrics=metrics,
                upstream_streaming_http_client=upstream_streaming_http_client,
                department=department,
                endpoint_name=endpoint_name,
                upstream_base_url=upstream_base_url,
                upstream_path=upstream_path,
                upstream_name=upstream_name,
                upstream_authorization_token=upstream_authorization_token,
                payload=payload,
                model_name=model_name,
                usage_extractor=usage_extractor,
                admission_controller=admission_controller,
                admission_lease=admission_lease,
            )
        except Exception:
            admission_controller.release(admission_lease)
            raise

    request_timeout = _request_timeout(config)

    try:
        upstream_base_url, upstream_authorization_token, upstream_name = _select_upstream(
            routing_registry=routing_registry,
            model_name=model_name,
            metrics=metrics,
            endpoint_name=endpoint_name,
        )
        attempted_upstream_names = {upstream_name}
        while True:
            try:
                upstream_request_started_at = time.perf_counter()
                upstream_response = await upstream_http_client.post(
                    f"{upstream_base_url}{upstream_path}",
                    json=payload,
                    headers=_build_upstream_headers(
                        request,
                        authorization_token=upstream_authorization_token,
                    ),
                    timeout=request_timeout,
                )
                metrics.observe_upstream_request_duration(
                    model_name=model_name,
                    upstream_name=upstream_name,
                    endpoint=endpoint_name,
                    duration_seconds=time.perf_counter() - upstream_request_started_at,
                )
                break
            except httpx.HTTPError as exc:
                if _is_connect_stage_retryable_error(exc):
                    try:
                        (
                            upstream_base_url,
                            upstream_authorization_token,
                            upstream_name,
                        ) = _select_upstream(
                            routing_registry=routing_registry,
                            model_name=model_name,
                            metrics=metrics,
                            endpoint_name=endpoint_name,
                            excluded_upstream_names=attempted_upstream_names,
                        )
                    except HTTPException:
                        _raise_upstream_transport_error(
                            metrics=metrics,
                            endpoint_name=endpoint_name,
                            exc=exc,
                            admission_controller=admission_controller,
                            department=department,
                            model_name=model_name,
                        )

                    attempted_upstream_names.add(upstream_name)
                    continue

                _raise_upstream_transport_error(
                    metrics=metrics,
                    endpoint_name=endpoint_name,
                    exc=exc,
                    admission_controller=admission_controller,
                    department=department,
                    model_name=model_name,
                )

        if upstream_response.status_code >= 400:
            set_request_metrics_failure_origin(request, failure_origin="upstream")
        if upstream_response.status_code >= 500:
            admission_controller.record_retry_event(department=department, model_name=model_name)

        try:
            response_payload = upstream_response.json()
        except ValueError:
            metrics.record_token_accounting(endpoint=endpoint_name, accounting_status="parse_error")
            return Response(
                content=upstream_response.content,
                status_code=upstream_response.status_code,
                media_type=upstream_response.headers.get("content-type"),
            )

        recorded_usage = _record_usage(
            metrics=metrics,
            department=department,
            endpoint_name=endpoint_name,
            model_name=model_name,
            upstream_status_code=upstream_response.status_code,
            usage=usage_extractor(response_payload),
        )
        if recorded_usage is not None:
            admission_controller.record_tokens(
                department=department,
                model_name=model_name,
                tokens=sum(recorded_usage),
            )

        return Response(
            content=upstream_response.content,
            status_code=upstream_response.status_code,
            media_type=upstream_response.headers.get("content-type", "application/json"),
        )
    finally:
        admission_controller.release(admission_lease)
