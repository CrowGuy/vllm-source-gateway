from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from vllm_source_gateway.config import AppConfig
from vllm_source_gateway.dependencies import get_app_config, get_routing_registry
from vllm_source_gateway.routing import RoutingRegistry

router = APIRouter(tags=["health"])


def _build_liveness_payload(config: AppConfig) -> dict[str, int | str]:
    return {
        "status": "ok",
        "upstream_count": len(config.upstreams),
        "model_count": len(config.model_names),
        "department_count": len(config.departments),
    }


def _build_readiness_payload(routing_registry: RoutingRegistry) -> tuple[int, dict[str, object]]:
    snapshots = routing_registry.health_snapshots()
    total_upstream_count = len(snapshots)
    healthy_upstream_count = sum(1 for snapshot in snapshots if snapshot.healthy)
    ready = healthy_upstream_count > 0

    return (
        status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        {
            "status": "ok" if ready else "not_ready",
            "healthy_upstream_count": healthy_upstream_count,
            "total_upstream_count": total_upstream_count,
            "upstreams": [
                {
                    "name": snapshot.upstream_name,
                    "healthy": snapshot.healthy,
                    "last_probe_at": snapshot.last_probe_at,
                    "last_success_at": snapshot.last_success_at,
                    "last_status_code": snapshot.last_status_code,
                    "last_error": snapshot.last_error,
                }
                for snapshot in snapshots
            ],
        },
    )


@router.get("/livez")
async def livez(config: AppConfig = Depends(get_app_config)) -> dict[str, int | str]:
    return _build_liveness_payload(config)


@router.get("/healthz")
async def healthz(config: AppConfig = Depends(get_app_config)) -> dict[str, int | str]:
    return _build_liveness_payload(config)


@router.get("/readyz")
async def readyz(
    routing_registry: RoutingRegistry = Depends(get_routing_registry),
) -> JSONResponse:
    status_code, payload = _build_readiness_payload(routing_registry)
    return JSONResponse(status_code=status_code, content=payload)
