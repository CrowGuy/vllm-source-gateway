from __future__ import annotations

from dataclasses import dataclass

import httpx
from fastapi import Depends, Header, HTTPException, Request, status

from vllm_source_gateway.config import AppConfig
from vllm_source_gateway.metrics import GatewayMetrics
from vllm_source_gateway.routing import RoutingRegistry


UNKNOWN_DEPARTMENT = "unknown"


class DependencyStateError(RuntimeError):
    """Raised when expected application state is missing."""


@dataclass(frozen=True, slots=True)
class SourceResolutionResult:
    department: str
    api_key: str | None
    resolution_source: str


def get_app_config(request: Request) -> AppConfig:
    config = getattr(request.app.state, "config", None)
    if config is None:
        raise DependencyStateError("application config is not loaded")
    return config


def get_routing_registry(request: Request) -> RoutingRegistry:
    registry = getattr(request.app.state, "routing_registry", None)
    if registry is None:
        raise DependencyStateError("routing registry is not loaded")
    return registry


def get_gateway_metrics(request: Request) -> GatewayMetrics:
    metrics = getattr(request.app.state, "metrics", None)
    if metrics is None:
        raise DependencyStateError("metrics registry is not loaded")
    return metrics


def get_upstream_http_client(request: Request) -> httpx.AsyncClient:
    client = getattr(request.app.state, "upstream_http_client", None)
    if client is None:
        raise DependencyStateError("upstream http client is not loaded")
    return client


def get_upstream_streaming_http_client(request: Request) -> httpx.AsyncClient:
    client = getattr(request.app.state, "upstream_streaming_http_client", None)
    if client is None:
        raise DependencyStateError("upstream streaming http client is not loaded")
    return client


def extract_api_key(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> str | None:
    if x_api_key:
        normalized = x_api_key.strip()
        return normalized or None

    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer":
            normalized = value.strip()
            return normalized or None

    return None


def resolve_department_for_api_key(config: AppConfig, api_key: str | None) -> SourceResolutionResult:
    if not api_key:
        return SourceResolutionResult(
            department=UNKNOWN_DEPARTMENT,
            api_key=None,
            resolution_source="unknown",
        )

    for department, department_config in config.departments.items():
        if api_key in department_config.api_keys:
            return SourceResolutionResult(
                department=department,
                api_key=api_key,
                resolution_source="api_key",
            )

    return SourceResolutionResult(
        department=UNKNOWN_DEPARTMENT,
        api_key=api_key,
        resolution_source="unknown",
    )


def require_api_key(
    api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> str:
    resolved = extract_api_key(authorization=authorization, x_api_key=api_key)
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing api key",
        )
    return resolved


def get_source_resolution_result(
    config: AppConfig = Depends(get_app_config),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> SourceResolutionResult:
    api_key = extract_api_key(authorization=authorization, x_api_key=x_api_key)
    return resolve_department_for_api_key(config, api_key)
