from __future__ import annotations

import copy
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


@pytest.fixture
def sample_config_dict() -> dict[str, object]:
    return {
        "server": {
            "host": "127.0.0.1",
            "port": 8080,
        },
        "timeouts": {
            "connect_seconds": 1.0,
            "upstream_request_seconds": 30.0,
            "stream_idle_seconds": 15.0,
        },
        "routing": {
            "strategy": "round_robin",
        },
        "upstreams": [
            {
                "name": "gpu-a",
                "base_url": "http://10.0.0.1:8000",
                "models": ["model-a", "shared-model"],
            },
            {
                "name": "gpu-b",
                "base_url": "http://10.0.0.2:8000",
                "models": ["model-b", "shared-model"],
            },
        ],
        "departments": {
            "dept-a": {
                "api_keys": ["key-dept-a"],
            },
            "dept-b": {
                "api_keys": ["key-dept-b"],
            },
        },
    }


@pytest.fixture
def sample_config_copy(sample_config_dict: dict[str, object]) -> dict[str, object]:
    return copy.deepcopy(sample_config_dict)


@pytest.fixture
def write_config(tmp_path: Path) -> Iterator:
    def _write(config_data: dict[str, object], filename: str = "config.yaml") -> Path:
        config_path = tmp_path / filename
        config_path.write_text(yaml.safe_dump(config_data, sort_keys=False), encoding="utf-8")
        return config_path

    yield _write


@pytest.fixture
def sample_config_path(
    sample_config_dict: dict[str, object],
    write_config,
) -> Path:
    return write_config(sample_config_dict)


@pytest.fixture
def app_client(sample_config_path: Path) -> Iterator[TestClient]:
    from vllm_source_gateway.main import create_app

    with TestClient(create_app(config_path=sample_config_path)) as client:
        yield client


class FakeUpstreamResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: dict[str, Any] | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        json_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = content if content is not None else json.dumps(payload).encode("utf-8")
        self.headers = headers or {"content-type": "application/json"}
        self._json_error = json_error

    def json(self) -> dict[str, Any]:
        if self._json_error is not None:
            raise self._json_error
        if self._payload is None:
            raise ValueError("no json payload configured")
        return self._payload


class FakeAsyncClient:
    def __init__(
        self,
        *,
        response: FakeUpstreamResponse | None = None,
        exception: Exception | None = None,
        recorder: dict[str, Any] | None = None,
        timeout: Any = None,
    ) -> None:
        self._response = response or FakeUpstreamResponse(payload={})
        self._exception = exception
        self._recorder = recorder if recorder is not None else {}
        self._recorder["timeout"] = timeout

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
    ) -> FakeUpstreamResponse:
        self._recorder["url"] = url
        self._recorder["json"] = json
        self._recorder["headers"] = headers
        if self._exception is not None:
            raise self._exception
        return self._response


@pytest.fixture
def install_fake_async_client(monkeypatch):
    from vllm_source_gateway.services import proxy

    def _install(
        *,
        response: FakeUpstreamResponse | None = None,
        exception: Exception | None = None,
    ) -> dict[str, Any]:
        recorder: dict[str, Any] = {}

        def _factory(*, timeout):
            return FakeAsyncClient(
                response=response,
                exception=exception,
                recorder=recorder,
                timeout=timeout,
            )

        monkeypatch.setattr(proxy.httpx, "AsyncClient", _factory)
        return recorder

    return _install
