from __future__ import annotations

import pytest

from vllm_source_gateway.config import ConfigError, load_config


def test_load_config_returns_validated_app_config(sample_config_path) -> None:
    config = load_config(sample_config_path)

    assert config.server.host == "127.0.0.1"
    assert config.server.port == 8080
    assert config.routing.strategy == "round_robin"
    assert config.model_names == ["model-a", "model-b", "shared-model"]
    assert config.departments["dept-a"].api_keys == ["key-dept-a"]


def test_load_config_raises_for_missing_file(tmp_path) -> None:
    missing_path = tmp_path / "missing.yaml"

    with pytest.raises(ConfigError, match="configuration file not found"):
        load_config(missing_path)


def test_load_config_rejects_duplicate_upstream_names(sample_config_copy, write_config) -> None:
    sample_config_copy["upstreams"][1]["name"] = "gpu-a"
    config_path = write_config(sample_config_copy, filename="duplicate-upstreams.yaml")

    with pytest.raises(ConfigError, match="upstream names must be unique"):
        load_config(config_path)


def test_load_config_rejects_duplicate_api_keys(sample_config_copy, write_config) -> None:
    sample_config_copy["departments"]["dept-b"]["api_keys"] = ["key-dept-a"]
    config_path = write_config(sample_config_copy, filename="duplicate-api-keys.yaml")

    with pytest.raises(ConfigError, match="assigned to more than one department"):
        load_config(config_path)


@pytest.mark.parametrize(
    ("mutation_path", "mutation_value"),
    [
        (("server", "extra_field"), "unexpected"),
        (("timeouts", "extra_field"), 123),
        (("health", "extra_field"), True),
        (("routing", "extra_field"), "unexpected"),
        (("security", "extra_field"), True),
        (("upstreams", 0, "extra_field"), "unexpected"),
        (("departments", "dept-a", "extra_field"), "unexpected"),
    ],
)
def test_load_config_rejects_unknown_nested_fields(
    sample_config_copy,
    write_config,
    mutation_path,
    mutation_value,
) -> None:
    target = sample_config_copy
    for key in mutation_path[:-1]:
        target = target[key]
    target[mutation_path[-1]] = mutation_value

    config_path = write_config(sample_config_copy, filename="unknown-nested-field.yaml")

    with pytest.raises(ConfigError, match="Extra inputs are not permitted"):
        load_config(config_path)
