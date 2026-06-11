from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from vllm_source_gateway.config import AppConfig
from vllm_source_gateway.dependencies import (
    SourceResolutionResult,
    get_app_config,
    get_gateway_metrics,
    get_routing_registry,
    get_source_resolution_result,
)
from vllm_source_gateway.metrics import GatewayMetrics
from vllm_source_gateway.routing import RoutingRegistry
from vllm_source_gateway.services.proxy import proxy_json_endpoint


router = APIRouter(prefix="/v1", tags=["responses"])

ENDPOINT_NAME = "responses"


def _extract_usage(payload: dict[str, Any]) -> tuple[int, int] | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None

    prompt_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    generation_tokens = usage.get("output_tokens", usage.get("completion_tokens"))

    if isinstance(prompt_tokens, int) and isinstance(generation_tokens, int):
        return prompt_tokens, generation_tokens

    return None


@router.post("/responses")
async def responses(
    request: Request,
    config: AppConfig = Depends(get_app_config),
    routing_registry: RoutingRegistry = Depends(get_routing_registry),
    metrics: GatewayMetrics = Depends(get_gateway_metrics),
    source_resolution: SourceResolutionResult = Depends(get_source_resolution_result),
) -> Response:
    return await proxy_json_endpoint(
        request=request,
        config=config,
        routing_registry=routing_registry,
        metrics=metrics,
        source_resolution=source_resolution,
        endpoint_name=ENDPOINT_NAME,
        upstream_path="/v1/responses",
        usage_extractor=_extract_usage,
    )
