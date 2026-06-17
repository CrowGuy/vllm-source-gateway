from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

import httpx

from vllm_source_gateway.config import AppConfig
from vllm_source_gateway.models import UpstreamTarget
from vllm_source_gateway.routing import RoutingRegistry


logger = logging.getLogger("vllm_source_gateway.upstream_health")


class UpstreamHealthMonitor:
    def __init__(self, *, config: AppConfig, routing_registry: RoutingRegistry) -> None:
        self._config = config
        self._routing_registry = routing_registry
        self._client: httpx.AsyncClient | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def enabled(self) -> bool:
        return self._config.health.enabled

    async def start(self) -> None:
        if not self.enabled:
            return

        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._config.health.request_timeout_seconds),
            )

        await self.refresh_all()
        self._task = asyncio.create_task(self._run(), name="upstream-health-monitor")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def refresh_all(self) -> None:
        if not self.enabled:
            return

        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._config.health.request_timeout_seconds),
            )

        for upstream in self._routing_registry.health_snapshots():
            upstream_target = self._routing_registry.get_upstream(upstream.upstream_name)
            healthy = await self._probe_upstream(upstream_target)
            self._routing_registry.set_upstream_health(upstream_target.name, healthy=healthy)

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._config.health.check_interval_seconds)
            await self.refresh_all()

    async def _probe_upstream(self, upstream: UpstreamTarget) -> bool:
        assert self._client is not None
        probe_url = f"{upstream.base_url}{self._config.health.probe_path}"
        headers = {}
        if upstream.authorization_token is not None:
            headers["authorization"] = f"Bearer {upstream.authorization_token}"

        try:
            response = await self._client.get(
                probe_url,
                headers=headers,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "upstream health probe failed",
                extra={"base_url": upstream.base_url, "error": str(exc)},
            )
            return False

        healthy = 200 <= response.status_code < 300
        if not healthy:
            logger.warning(
                "upstream health probe returned non-2xx",
                extra={"base_url": upstream.base_url, "status_code": response.status_code},
            )
        return healthy
