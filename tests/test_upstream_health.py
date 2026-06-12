from __future__ import annotations

import httpx

from vllm_source_gateway.config import AppConfig
from vllm_source_gateway.routing import RoutingRegistry
from vllm_source_gateway.services.upstream_health import UpstreamHealthMonitor


class FakeHealthResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class FakeHealthAsyncClient:
    def __init__(self, responses_by_url, recorder, **_kwargs) -> None:
        self._responses_by_url = responses_by_url
        self._recorder = recorder
        self._recorder["closed"] = False

    async def get(self, url: str, *, follow_redirects: bool) -> FakeHealthResponse:
        self._recorder.setdefault("requests", []).append((url, follow_redirects))
        result = self._responses_by_url[url]
        if isinstance(result, Exception):
            raise result
        return result

    async def aclose(self) -> None:
        self._recorder["closed"] = True


async def test_upstream_health_monitor_marks_upstreams_healthy_and_unhealthy(monkeypatch, sample_config_dict) -> None:
    config = AppConfig.model_validate(sample_config_dict)
    registry = RoutingRegistry.from_config(config)
    recorder: dict[str, object] = {}
    responses_by_url = {
        "http://10.0.0.1:8000/v1/models": FakeHealthResponse(200),
        "http://10.0.0.2:8000/v1/models": httpx.ConnectError("connection failed"),
    }

    monkeypatch.setattr(
        "vllm_source_gateway.services.upstream_health.httpx.AsyncClient",
        lambda **kwargs: FakeHealthAsyncClient(responses_by_url, recorder, **kwargs),
    )

    monitor = UpstreamHealthMonitor(config=config, routing_registry=registry)
    await monitor.refresh_all()

    snapshots = {snapshot.upstream_name: snapshot.healthy for snapshot in registry.health_snapshots()}

    assert snapshots == {
        "gpu-a": True,
        "gpu-b": False,
    }
    assert recorder["requests"] == [
        ("http://10.0.0.1:8000/v1/models", True),
        ("http://10.0.0.2:8000/v1/models", True),
    ]


async def test_upstream_health_monitor_is_noop_when_disabled(monkeypatch, sample_config_copy) -> None:
    sample_config_copy["health"]["enabled"] = False
    config = AppConfig.model_validate(sample_config_copy)
    registry = RoutingRegistry.from_config(config)
    called = False

    def _unexpected_client(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("health client should not be created when health checks are disabled")

    monkeypatch.setattr(
        "vllm_source_gateway.services.upstream_health.httpx.AsyncClient",
        _unexpected_client,
    )

    monitor = UpstreamHealthMonitor(config=config, routing_registry=registry)
    await monitor.refresh_all()

    assert called is False
    snapshots = {snapshot.upstream_name: snapshot.healthy for snapshot in registry.health_snapshots()}
    assert snapshots == {
        "gpu-a": True,
        "gpu-b": True,
    }
