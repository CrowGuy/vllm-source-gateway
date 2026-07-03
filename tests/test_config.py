from __future__ import annotations

import pytest

from vllm_source_gateway.config import ConfigError, load_config


def test_load_config_returns_validated_app_config(sample_config_path) -> None:
    config = load_config(sample_config_path)

    assert config.server.host == "127.0.0.1"
    assert config.server.port == 8080
    assert config.server.max_request_body_bytes == 4194304
    assert config.server.max_sse_decode_buffer_bytes == 262144
    assert config.routing.strategy == "round_robin"
    assert config.model_names == ["model-a", "model-b", "shared-model"]
    assert config.departments["dept-a"].api_keys == ["key-dept-a"]
    assert config.upstreams[0].authorization_token == "upstream-token-a"
    assert config.upstreams[1].authorization_token == "upstream-token-b"


def test_load_config_resolves_department_api_keys_from_env(
    monkeypatch,
    sample_config_copy,
    write_config,
) -> None:
    sample_config_copy["departments"]["dept-a"] = {"api_keys_from_env": "DEPT_A_KEYS"}
    monkeypatch.setenv("DEPT_A_KEYS", "key-dept-a,key-dept-a-2")
    config_path = write_config(sample_config_copy, filename="api-keys-from-env.yaml")

    config = load_config(config_path)

    assert config.departments["dept-a"].api_keys == ["key-dept-a", "key-dept-a-2"]


def test_load_config_resolves_department_api_keys_from_json_array_env(
    monkeypatch,
    sample_config_copy,
    write_config,
) -> None:
    sample_config_copy["departments"]["dept-a"] = {"api_keys_from_env": "DEPT_A_KEYS"}
    monkeypatch.setenv("DEPT_A_KEYS", '["key-dept-a","key-dept-a-2"]')
    config_path = write_config(sample_config_copy, filename="api-keys-from-json-env.yaml")

    config = load_config(config_path)

    assert config.departments["dept-a"].api_keys == ["key-dept-a", "key-dept-a-2"]


def test_load_config_resolves_upstream_authorization_from_env(
    monkeypatch,
    sample_config_copy,
    write_config,
) -> None:
    sample_config_copy["upstreams"][0]["authorization_from_env"] = "UPSTREAM_MODEL_A_TOKEN"
    monkeypatch.setenv("UPSTREAM_MODEL_A_TOKEN", "prod-token-a")
    config_path = write_config(sample_config_copy, filename="upstream-auth-from-env.yaml")

    config = load_config(config_path)

    assert config.upstreams[0].authorization_token == "prod-token-a"


def test_load_config_accepts_model_catalog(sample_config_copy, write_config) -> None:
    sample_config_copy["model_catalog"] = {
        "model-a": {
            "display_name": "Model A",
            "hosted_on": "DGX-A100",
            "use_cases": [" coding ", "general chat"],
            "api_paths": ["chat", "responses", "messages"],
            "context_window": " 32k tokens ",
            "recommended_for": ["code agents"],
            "known_limits": ["not for embeddings"],
            "example_prompt": " Say hello. ",
        }
    }
    config_path = write_config(sample_config_copy, filename="model-catalog.yaml")

    config = load_config(config_path)

    catalog_entry = config.model_catalog["model-a"]
    assert catalog_entry.display_name == "Model A"
    assert catalog_entry.hosted_on == "DGX-A100"
    assert catalog_entry.use_cases == ["coding", "general chat"]
    assert catalog_entry.api_paths == ["chat", "responses", "messages"]
    assert catalog_entry.context_window == "32k tokens"
    assert catalog_entry.recommended_for == ["code agents"]
    assert catalog_entry.known_limits == ["not for embeddings"]
    assert catalog_entry.example_prompt == "Say hello."


def test_load_config_rejects_unknown_model_catalog_fields(sample_config_copy, write_config) -> None:
    sample_config_copy["model_catalog"] = {
        "model-a": {
            "display_name": "Model A",
            "unexpected": "field",
        }
    }
    config_path = write_config(sample_config_copy, filename="unknown-model-catalog-field.yaml")

    with pytest.raises(ConfigError, match="Extra inputs are not permitted"):
        load_config(config_path)


def test_load_config_rejects_unsupported_model_catalog_api_paths(
    sample_config_copy,
    write_config,
) -> None:
    sample_config_copy["model_catalog"] = {
        "model-a": {
            "display_name": "Model A",
            "api_paths": ["chat", "embeddings"],
        }
    }
    config_path = write_config(sample_config_copy, filename="unsupported-catalog-api-path.yaml")

    with pytest.raises(ConfigError, match="api_paths may only include supported gateway paths"):
        load_config(config_path)


@pytest.mark.parametrize("hosted_on", ["http://10.0.0.1:8000", "10.0.0.1"])
def test_load_config_rejects_model_catalog_hosted_on_urls_or_ips(
    sample_config_copy,
    write_config,
    hosted_on,
) -> None:
    sample_config_copy["model_catalog"] = {
        "model-a": {
            "display_name": "Model A",
            "hosted_on": hosted_on,
        }
    }
    config_path = write_config(sample_config_copy, filename="unsafe-catalog-hosted-on.yaml")

    with pytest.raises(ConfigError, match="hosted_on must be an abstract location description"):
        load_config(config_path)


def test_load_config_rejects_missing_upstream_authorization_env(
    monkeypatch,
    sample_config_copy,
    write_config,
) -> None:
    sample_config_copy["upstreams"][0]["authorization_from_env"] = "UPSTREAM_MODEL_A_TOKEN"
    monkeypatch.delenv("UPSTREAM_MODEL_A_TOKEN", raising=False)
    config_path = write_config(sample_config_copy, filename="missing-upstream-auth-env.yaml")

    with pytest.raises(ConfigError, match="authorization_from_env 'UPSTREAM_MODEL_A_TOKEN' is not set"):
        load_config(config_path)


def test_load_config_rejects_empty_upstream_authorization_env(
    monkeypatch,
    sample_config_copy,
    write_config,
) -> None:
    sample_config_copy["upstreams"][0]["authorization_from_env"] = "UPSTREAM_MODEL_A_TOKEN"
    monkeypatch.setenv("UPSTREAM_MODEL_A_TOKEN", "   ")
    config_path = write_config(sample_config_copy, filename="empty-upstream-auth-env.yaml")

    with pytest.raises(ConfigError, match="authorization_from_env 'UPSTREAM_MODEL_A_TOKEN' is empty"):
        load_config(config_path)


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


def test_load_config_rejects_department_with_both_inline_and_env_api_keys(
    sample_config_copy,
    write_config,
) -> None:
    sample_config_copy["departments"]["dept-a"]["api_keys_from_env"] = "DEPT_A_KEYS"
    config_path = write_config(sample_config_copy, filename="both-api-key-sources.yaml")

    with pytest.raises(ConfigError, match="exactly one of api_keys or api_keys_from_env"):
        load_config(config_path)


def test_load_config_rejects_missing_department_api_keys_env(
    sample_config_copy,
    write_config,
) -> None:
    sample_config_copy["departments"]["dept-a"] = {"api_keys_from_env": "DEPT_A_KEYS"}
    config_path = write_config(sample_config_copy, filename="missing-api-keys-env.yaml")

    with pytest.raises(ConfigError, match="api_keys_from_env 'DEPT_A_KEYS' is not set"):
        load_config(config_path)


def test_load_config_rejects_empty_department_api_keys_env(
    monkeypatch,
    sample_config_copy,
    write_config,
) -> None:
    sample_config_copy["departments"]["dept-a"] = {"api_keys_from_env": "DEPT_A_KEYS"}
    monkeypatch.setenv("DEPT_A_KEYS", "   ")
    config_path = write_config(sample_config_copy, filename="empty-api-keys-env.yaml")

    with pytest.raises(ConfigError, match="api_keys_from_env 'DEPT_A_KEYS' is empty"):
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
