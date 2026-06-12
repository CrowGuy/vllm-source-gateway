from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from vllm_source_gateway.config import (
    DEFAULT_CONFIG_ENV_VAR,
    load_config,
)
from vllm_source_gateway.metrics import GatewayMetrics
from vllm_source_gateway.routers.chat_completions import router as chat_completions_router
from vllm_source_gateway.routers.health import router as health_router
from vllm_source_gateway.routers.models import router as models_router
from vllm_source_gateway.routers.responses import router as responses_router
from vllm_source_gateway.routing import RoutingRegistry
from vllm_source_gateway.services.upstream_health import UpstreamHealthMonitor


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("vllm_source_gateway")


def _resolve_config_path(config_path: str | Path | None) -> Path:
    if config_path is not None:
        return Path(config_path)
    return Path(os.environ.get(DEFAULT_CONFIG_ENV_VAR, "config.yaml"))


def create_app(config_path: str | Path | None = None) -> FastAPI:
    resolved_config_path = _resolve_config_path(config_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = load_config(resolved_config_path)
        routing_registry = RoutingRegistry.from_config(config)
        metrics = GatewayMetrics()
        upstream_health_monitor = UpstreamHealthMonitor(
            config=config,
            routing_registry=routing_registry,
        )
        await upstream_health_monitor.start()
        app.state.config = config
        app.state.config_path = str(resolved_config_path)
        app.state.routing_registry = routing_registry
        app.state.metrics = metrics
        app.state.upstream_health_monitor = upstream_health_monitor

        logger.info(
            "gateway configuration loaded",
            extra={
                "config_path": str(resolved_config_path),
                "upstream_count": len(config.upstreams),
                "model_count": len(config.model_names),
                "department_count": len(config.departments),
            },
        )
        yield
        await upstream_health_monitor.stop()

    app = FastAPI(
        title="vllm-source-gateway",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(health_router)
    app.include_router(models_router)
    app.include_router(chat_completions_router)
    app.include_router(responses_router)

    @app.get("/metrics")
    async def metrics(request: Request) -> PlainTextResponse:
        metrics_registry: GatewayMetrics = request.app.state.metrics
        return PlainTextResponse(
            generate_latest(metrics_registry.registry),
            media_type=CONTENT_TYPE_LATEST,
        )

    return app


app = create_app()
