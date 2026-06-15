from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from vllm_source_gateway.config import (
    DEFAULT_CONFIG_ENV_VAR,
    load_config,
)
from vllm_source_gateway.logging_utils import configure_application_logging, log_request_completion
from vllm_source_gateway.metrics import GatewayMetrics
from vllm_source_gateway.request_metrics import (
    finalize_request_metrics,
    initialize_request_metrics_state,
    set_request_metrics_status_override,
)
from vllm_source_gateway.routers.chat_completions import router as chat_completions_router
from vllm_source_gateway.routers.health import router as health_router
from vllm_source_gateway.routers.models import router as models_router
from vllm_source_gateway.routers.responses import router as responses_router
from vllm_source_gateway.routing import RoutingRegistry
from vllm_source_gateway.services.upstream_health import UpstreamHealthMonitor


logger = configure_application_logging(level=logging.INFO)


UPSTREAM_CLIENT_LIMITS = httpx.Limits(
    max_connections=200,
    max_keepalive_connections=50,
)


def _build_streaming_timeout(config) -> httpx.Timeout:
    return httpx.Timeout(
        connect=config.timeouts.connect_seconds,
        read=config.timeouts.stream_idle_seconds,
        write=config.timeouts.upstream_request_seconds,
        pool=config.timeouts.upstream_request_seconds,
    )


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
        upstream_http_client = httpx.AsyncClient(limits=UPSTREAM_CLIENT_LIMITS)
        upstream_streaming_http_client = httpx.AsyncClient(
            limits=UPSTREAM_CLIENT_LIMITS,
            timeout=_build_streaming_timeout(config),
        )
        upstream_health_monitor = UpstreamHealthMonitor(
            config=config,
            routing_registry=routing_registry,
        )
        await upstream_health_monitor.start()
        app.state.config = config
        app.state.config_path = str(resolved_config_path)
        app.state.routing_registry = routing_registry
        app.state.metrics = metrics
        app.state.upstream_http_client = upstream_http_client
        app.state.upstream_streaming_http_client = upstream_streaming_http_client
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
        await upstream_http_client.aclose()
        await upstream_streaming_http_client.aclose()

    app = FastAPI(
        title="vllm-source-gateway",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_metrics_middleware(request: Request, call_next):
        initialize_request_metrics_state(request)

        try:
            response = await call_next(request)
        except Exception:
            snapshot = finalize_request_metrics(request, default_status_code=500)
            log_request_completion(request=request, snapshot=snapshot)
            raise

        if isinstance(response, StreamingResponse):
            original_body_iterator = response.body_iterator

            async def _wrap_streaming_body() -> AsyncIterator[bytes]:
                try:
                    async for chunk in original_body_iterator:
                        yield chunk
                except Exception:
                    set_request_metrics_status_override(request, status_code=500)
                    raise
                finally:
                    snapshot = finalize_request_metrics(
                        request,
                        default_status_code=response.status_code,
                    )
                    log_request_completion(request=request, snapshot=snapshot)

            response.body_iterator = _wrap_streaming_body()
            return response

        snapshot = finalize_request_metrics(request, default_status_code=response.status_code)
        log_request_completion(request=request, snapshot=snapshot)
        return response

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
