from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from vllm_source_gateway.config import AppConfig
from vllm_source_gateway.dependencies import get_app_config, get_routing_registry
from vllm_source_gateway.rendering.catalog import render_catalog_page
from vllm_source_gateway.routing import RoutingRegistry

router = APIRouter(tags=["catalog"])


@router.get("/models", response_class=HTMLResponse)
async def model_catalog(
    config: AppConfig = Depends(get_app_config),
    routing_registry: RoutingRegistry = Depends(get_routing_registry),
) -> HTMLResponse:
    html = render_catalog_page(
        models=routing_registry.list_models(),
        model_catalog=config.model_catalog,
    )
    return HTMLResponse(content=html)
