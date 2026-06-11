from __future__ import annotations

from fastapi import APIRouter, Depends

from vllm_source_gateway.dependencies import get_routing_registry
from vllm_source_gateway.routing import RoutingRegistry


router = APIRouter(prefix="/v1", tags=["models"])


@router.get("/models")
async def list_models(
    routing_registry: RoutingRegistry = Depends(get_routing_registry),
) -> dict[str, object]:
    models = routing_registry.list_models()
    return {
        "object": "list",
        "data": [
            {
                "id": model.model_name,
                "object": "model",
                "status": model.status,
                "healthy_upstreams": model.healthy_upstream_count,
                "total_upstreams": model.total_upstream_count,
            }
            for model in models
        ],
    }

