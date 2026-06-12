from __future__ import annotations

import asyncio
import codecs
import json
import time
from typing import Any, Callable

import httpx
from fastapi import HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from vllm_source_gateway.config import AppConfig
from vllm_source_gateway.dependencies import SourceResolutionResult
from vllm_source_gateway.metrics import GatewayMetrics
from vllm_source_gateway.routing import NoHealthyUpstreamError, RoutingRegistry, UnknownModelError

UsageExtractor = Callable[[dict[str, Any]], tuple[int, int] | None]

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


def _build_upstream_headers(request: Request) -> dict[str, str]:
    return {
        key: value
        for key, value in request.headers.items()
        if _should_forward_upstream_header(key)
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


def _record_usage(
    *,
    metrics: GatewayMetrics,
    department: str,
    endpoint_name: str,
    model_name: str,
    upstream_status_code: int,
    usage: tuple[int, int] | None,
) -> None:
    if not 200 <= upstream_status_code < 300:
        metrics.record_token_accounting(endpoint=endpoint_name, accounting_status="missing_usage")
        return

    if usage is None:
        metrics.record_token_accounting(endpoint=endpoint_name, accounting_status="missing_usage")
        return

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


def _build_downstream_headers(upstream_headers: httpx.Headers) -> dict[str, str]:
    return {
        key: value
        for key, value in upstream_headers.items()
        if key.lower() not in _EXCLUDED_DOWNSTREAM_HEADERS
    }


def _parse_json_payload(
    *,
    payload: Any,
    metrics: GatewayMetrics,
    department: str,
    endpoint_name: str,
    method: str,
    started_at: float,
) -> tuple[dict[str, Any], str]:
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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            started_at=started_at,
            detail="missing model",
        )

    return payload, model_name


def _select_upstream(
    *,
    routing_registry: RoutingRegistry,
    model_name: str,
    metrics: GatewayMetrics,
    department: str,
    endpoint_name: str,
    method: str,
    started_at: float,
) -> str:
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

    return selected.upstream.base_url


def _consume_sse_events(
    *,
    buffer: str,
    fragment: str,
    usage_extractor: UsageExtractor,
    latest_usage: tuple[int, int] | None,
) -> tuple[str, tuple[int, int] | None]:
    normalized_buffer = (buffer + fragment).replace("\r\n", "\n")

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

    return normalized_buffer, latest_usage


def _request_timeout(config: AppConfig) -> httpx.Timeout:
    return httpx.Timeout(
        timeout=config.timeouts.upstream_request_seconds,
        connect=config.timeouts.connect_seconds,
    )


async def _proxy_streaming_response(
    *,
    request: Request,
    metrics: GatewayMetrics,
    upstream_streaming_http_client: httpx.AsyncClient,
    department: str,
    endpoint_name: str,
    upstream_url: str,
    payload: dict[str, Any],
    model_name: str,
    method: str,
    started_at: float,
    usage_extractor: UsageExtractor,
) -> Response:
    request_headers = _build_upstream_headers(request)

    try:
        upstream_request = upstream_streaming_http_client.build_request(
            "POST",
            upstream_url,
            json=payload,
            headers=request_headers,
        )
        upstream_response = await upstream_streaming_http_client.send(
            upstream_request,
            stream=True,
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

    async def _stream_bytes():
        latest_usage: tuple[int, int] | None = None
        decode_buffer = ""
        parsing_enabled = True
        decoder = codecs.getincrementaldecoder("utf-8")()
        client_disconnected = False
        stream_failed = False

        try:
            async for chunk in upstream_response.aiter_bytes():
                if await request.is_disconnected():
                    client_disconnected = True
                    break

                if parsing_enabled and chunk:
                    try:
                        fragment = decoder.decode(chunk)
                    except UnicodeDecodeError:
                        parsing_enabled = False
                    else:
                        decode_buffer, latest_usage = _consume_sse_events(
                            buffer=decode_buffer,
                            fragment=fragment,
                            usage_extractor=usage_extractor,
                            latest_usage=latest_usage,
                        )

                yield chunk

            if parsing_enabled and not client_disconnected:
                try:
                    final_fragment = decoder.decode(b"", final=True)
                except UnicodeDecodeError:
                    parsing_enabled = False
                else:
                    decode_buffer, latest_usage = _consume_sse_events(
                        buffer=decode_buffer,
                        fragment=final_fragment,
                        usage_extractor=usage_extractor,
                        latest_usage=latest_usage,
                    )
        except asyncio.CancelledError:
            client_disconnected = True
            raise
        except httpx.HTTPError:
            stream_failed = True
            raise
        finally:
            await upstream_response.aclose()

            if client_disconnected:
                metrics.record_token_accounting(
                    endpoint=endpoint_name,
                    accounting_status="missing_usage",
                )
                _record_request(
                    metrics=metrics,
                    department=department,
                    endpoint=endpoint_name,
                    method=method,
                    status_code=_CLIENT_DISCONNECTED_STATUS,
                    started_at=started_at,
                )
                return

            if stream_failed:
                metrics.record_token_accounting(
                    endpoint=endpoint_name,
                    accounting_status="missing_usage",
                )
                _record_request(
                    metrics=metrics,
                    department=department,
                    endpoint=endpoint_name,
                    method=method,
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    started_at=started_at,
                )
                return

            _record_usage(
                metrics=metrics,
                department=department,
                endpoint_name=endpoint_name,
                model_name=model_name,
                upstream_status_code=upstream_response.status_code,
                usage=latest_usage,
            )
            _record_request(
                metrics=metrics,
                department=department,
                endpoint=endpoint_name,
                method=method,
                status_code=upstream_response.status_code,
                started_at=started_at,
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
    source_resolution: SourceResolutionResult,
    upstream_http_client: httpx.AsyncClient,
    upstream_streaming_http_client: httpx.AsyncClient,
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

    payload, model_name = _parse_json_payload(
        payload=payload,
        metrics=metrics,
        department=department,
        endpoint_name=endpoint_name,
        method=method,
        started_at=started_at,
    )
    upstream_base_url = _select_upstream(
        routing_registry=routing_registry,
        model_name=model_name,
        metrics=metrics,
        department=department,
        endpoint_name=endpoint_name,
        method=method,
        started_at=started_at,
    )

    if payload.get("stream") is True:
        return await _proxy_streaming_response(
            request=request,
            metrics=metrics,
            upstream_streaming_http_client=upstream_streaming_http_client,
            department=department,
            endpoint_name=endpoint_name,
            upstream_url=f"{upstream_base_url}{upstream_path}",
            payload=payload,
            model_name=model_name,
            method=method,
            started_at=started_at,
            usage_extractor=usage_extractor,
        )

    try:
        upstream_response = await upstream_http_client.post(
            f"{upstream_base_url}{upstream_path}",
            json=payload,
            headers=_build_upstream_headers(request),
            timeout=_request_timeout(config),
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

    _record_usage(
        metrics=metrics,
        department=department,
        endpoint_name=endpoint_name,
        model_name=model_name,
        upstream_status_code=upstream_response.status_code,
        usage=usage_extractor(response_payload),
    )

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
