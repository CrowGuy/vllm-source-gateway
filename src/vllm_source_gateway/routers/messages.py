from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request, Response

from vllm_source_gateway.config import AppConfig
from vllm_source_gateway.dependencies import (
    SourceResolutionResult,
    get_app_config,
    get_admission_controller,
    get_gateway_metrics,
    get_routing_registry,
    get_source_resolution_result,
    get_upstream_http_client,
    get_upstream_streaming_http_client,
)
from vllm_source_gateway.metrics import GatewayMetrics
from vllm_source_gateway.routing import RoutingRegistry
from vllm_source_gateway.services.admission_control import AdmissionController
from vllm_source_gateway.services.proxy import proxy_json_endpoint


router = APIRouter(prefix="/v1", tags=["messages"])

ENDPOINT_NAME = "messages"


def _extract_usage(payload: dict[str, Any]) -> tuple[int, int] | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None

    prompt_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    generation_tokens = usage.get("output_tokens", usage.get("completion_tokens"))

    if isinstance(prompt_tokens, int) and isinstance(generation_tokens, int):
        return prompt_tokens, generation_tokens

    message = payload.get("message")
    if isinstance(message, dict):
        message_usage = message.get("usage")
        if isinstance(message_usage, dict):
            prompt_tokens = message_usage.get("input_tokens", message_usage.get("prompt_tokens"))
            generation_tokens = message_usage.get("output_tokens", message_usage.get("completion_tokens"))
            if isinstance(prompt_tokens, int) and isinstance(generation_tokens, int):
                return prompt_tokens, generation_tokens

    return None


@router.post("/messages")
async def messages(
    request: Request,
    config: AppConfig = Depends(get_app_config),
    routing_registry: RoutingRegistry = Depends(get_routing_registry),
    metrics: GatewayMetrics = Depends(get_gateway_metrics),
    admission_controller: AdmissionController = Depends(get_admission_controller),
    source_resolution: SourceResolutionResult = Depends(get_source_resolution_result),
    upstream_http_client: httpx.AsyncClient = Depends(get_upstream_http_client),
    upstream_streaming_http_client: httpx.AsyncClient = Depends(get_upstream_streaming_http_client),
) -> Response:
    return await proxy_json_endpoint(
        request=request,
        config=config,
        routing_registry=routing_registry,
        metrics=metrics,
        admission_controller=admission_controller,
        source_resolution=source_resolution,
        upstream_http_client=upstream_http_client,
        upstream_streaming_http_client=upstream_streaming_http_client,
        endpoint_name=ENDPOINT_NAME,
        upstream_path="/v1/messages",
        usage_extractor=_extract_usage,
    )
