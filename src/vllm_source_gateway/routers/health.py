from __future__ import annotations

from fastapi import APIRouter, Depends

from vllm_source_gateway.config import AppConfig
from vllm_source_gateway.dependencies import get_app_config


router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(config: AppConfig = Depends(get_app_config)) -> dict[str, int | str]:
    return {
        "status": "ok",
        "upstream_count": len(config.upstreams),
        "model_count": len(config.model_names),
        "department_count": len(config.departments),
    }

