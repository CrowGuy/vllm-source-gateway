from __future__ import annotations

import copy
import sys
from collections.abc import Iterator
from pathlib import Path

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
