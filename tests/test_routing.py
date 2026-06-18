from __future__ import annotations

import pytest

from vllm_source_gateway.config import AppConfig
from vllm_source_gateway.routing import NoHealthyUpstreamError, RoutingRegistry, UnknownModelError


def test_routing_registry_builds_model_index(sample_config_dict) -> None:
    config = AppConfig.model_validate(sample_config_dict)
    registry = RoutingRegistry.from_config(config)

    assert registry.model_names == ["model-a", "model-b", "shared-model"]
    shared_model_upstreams = registry.get_model_upstreams("shared-model")

    assert [upstream.name for upstream in shared_model_upstreams] == ["gpu-a", "gpu-b"]
    assert shared_model_upstreams[0].authorization_token == "upstream-token-a"
    assert shared_model_upstreams[1].authorization_token == "upstream-token-b"


def test_routing_registry_round_robins_across_same_model_upstreams(sample_config_dict) -> None:
    config = AppConfig.model_validate(sample_config_dict)
    registry = RoutingRegistry.from_config(config)

    selections = [registry.select_upstream("shared-model").upstream.name for _ in range(3)]

    assert selections == ["gpu-a", "gpu-b", "gpu-a"]


def test_routing_registry_skips_unhealthy_upstreams(sample_config_dict) -> None:
    config = AppConfig.model_validate(sample_config_dict)
    registry = RoutingRegistry.from_config(config)
    registry.set_upstream_health("gpu-a", healthy=False)

    selection = registry.select_upstream("shared-model")

    assert selection.upstream.name == "gpu-b"


def test_routing_registry_skips_excluded_upstreams(sample_config_dict) -> None:
    config = AppConfig.model_validate(sample_config_dict)
    registry = RoutingRegistry.from_config(config)

    selection = registry.select_upstream(
        "shared-model",
        excluded_upstream_names={"gpu-a"},
    )

    assert selection.upstream.name == "gpu-b"


def test_routing_registry_raises_when_model_has_no_healthy_upstream(sample_config_dict) -> None:
    config = AppConfig.model_validate(sample_config_dict)
    registry = RoutingRegistry.from_config(config)
    registry.set_upstream_health("gpu-a", healthy=False)
    registry.set_upstream_health("gpu-b", healthy=False)

    with pytest.raises(NoHealthyUpstreamError, match="no healthy upstream available"):
        registry.select_upstream("shared-model")


def test_routing_registry_raises_for_unknown_model(sample_config_dict) -> None:
    config = AppConfig.model_validate(sample_config_dict)
    registry = RoutingRegistry.from_config(config)

    with pytest.raises(UnknownModelError, match="unknown model 'missing-model'"):
        registry.select_upstream("missing-model")


def test_routing_registry_preserves_round_robin_order_when_health_changes(sample_config_copy) -> None:
    sample_config_copy["upstreams"].append(
        {
            "name": "gpu-c",
            "base_url": "http://10.0.0.3:8000",
            "models": ["shared-model"],
        }
    )
    config = AppConfig.model_validate(sample_config_copy)
    registry = RoutingRegistry.from_config(config)

    initial_selections = [registry.select_upstream("shared-model").upstream.name for _ in range(2)]
    registry.set_upstream_health("gpu-b", healthy=False)
    selection_after_health_change = registry.select_upstream("shared-model")

    assert initial_selections == ["gpu-a", "gpu-b"]
    assert selection_after_health_change.upstream.name == "gpu-c"
