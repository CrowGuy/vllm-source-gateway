from __future__ import annotations

from vllm_source_gateway.config import AppConfig
from vllm_source_gateway.dependencies import (
    UNKNOWN_DEPARTMENT,
    extract_api_key,
    resolve_department_for_api_key,
)

from conftest import FakeUpstreamResponse, TestClient


def test_extract_api_key_prefers_x_api_key_over_authorization() -> None:
    api_key = extract_api_key(
        authorization="Bearer bearer-token",
        x_api_key="header-key",
    )

    assert api_key == "header-key"


def test_extract_api_key_reads_bearer_authorization() -> None:
    api_key = extract_api_key(
        authorization="Bearer bearer-token",
        x_api_key=None,
    )

    assert api_key == "bearer-token"


def test_resolve_department_for_api_key_returns_mapped_department(sample_config_dict) -> None:
    config = AppConfig.model_validate(sample_config_dict)

    result = resolve_department_for_api_key(config, "key-dept-a")

    assert result.department == "dept-a"
    assert result.api_key == "key-dept-a"
    assert result.resolution_source == "api_key"


def test_app_config_precomputes_api_key_lookup(sample_config_dict) -> None:
    config = AppConfig.model_validate(sample_config_dict)

    assert config.api_key_to_department == {
        "key-dept-a": "dept-a",
        "key-dept-b": "dept-b",
    }


def test_app_config_precomputes_api_key_lookup_from_env(monkeypatch, sample_config_dict) -> None:
    sample_config_dict["departments"]["dept-a"] = {"api_keys_from_env": "DEPT_A_KEYS"}
    monkeypatch.setenv("DEPT_A_KEYS", "key-dept-a,key-dept-a-2")

    config = AppConfig.model_validate(sample_config_dict)

    assert config.api_key_to_department == {
        "key-dept-a": "dept-a",
        "key-dept-a-2": "dept-a",
        "key-dept-b": "dept-b",
    }


def test_resolve_department_for_api_key_returns_unknown_for_missing_key(sample_config_dict) -> None:
    config = AppConfig.model_validate(sample_config_dict)

    result = resolve_department_for_api_key(config, None)

    assert result.department == UNKNOWN_DEPARTMENT
    assert result.api_key is None
    assert result.resolution_source == "unknown"


def test_resolve_department_for_api_key_returns_unknown_for_unmapped_key(sample_config_dict) -> None:
    config = AppConfig.model_validate(sample_config_dict)

    result = resolve_department_for_api_key(config, "missing-key")

    assert result.department == UNKNOWN_DEPARTMENT
    assert result.api_key == "missing-key"
    assert result.resolution_source == "unknown"


def test_chat_completions_records_unknown_source_resolution_for_unmapped_key(
    app_client,
    install_fake_async_client,
) -> None:
    install_fake_async_client(
        app_client=app_client,
        response=FakeUpstreamResponse(
            payload={
                "id": "chatcmpl-unknown",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 5},
            }
        )
    )

    response = app_client.post(
        "/v1/chat/completions",
        headers={"x-api-key": "unmapped-key"},
        json={
            "model": "shared-model",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 200

    metrics_text = app_client.get("/metrics").text

    assert (
        'gateway_source_resolution_total{department="unknown",resolution_source="unknown"} 1.0'
        in metrics_text
    )
    assert (
        'gateway_prompt_tokens_total{department="unknown",model_name="shared-model"} 3.0'
        in metrics_text
    )
    assert (
        'gateway_generation_tokens_total{department="unknown",model_name="shared-model"} 5.0'
        in metrics_text
    )


def test_chat_completions_rejects_unmapped_key_when_strict_mode_enabled(
    sample_config_copy,
    write_config,
    install_fake_async_client,
) -> None:
    from vllm_source_gateway.main import create_app

    sample_config_copy["security"]["reject_unknown_api_keys"] = True
    config_path = write_config(sample_config_copy, filename="strict-unknown-keys.yaml")

    with TestClient(create_app(config_path=config_path)) as app_client:
        install_fake_async_client(
            app_client=app_client,
            response=FakeUpstreamResponse(
                payload={
                    "id": "chatcmpl-should-not-proxy",
                    "object": "chat.completion",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 5},
                }
            ),
        )

        response = app_client.post(
            "/v1/chat/completions",
            headers={"x-api-key": "unmapped-key"},
            json={
                "model": "shared-model",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

        assert response.status_code == 401
        assert response.json() == {"detail": "unknown api key"}

        metrics_text = app_client.get("/metrics").text

        assert (
            'gateway_source_resolution_total{department="unknown",resolution_source="unknown"} 1.0'
            in metrics_text
        )
        assert (
            'gateway_http_requests_total{department="unknown",endpoint="chat_completions",method="POST",status_class="4xx"} 1.0'
            in metrics_text
        )
        assert 'gateway_prompt_tokens_total{department="unknown",model_name="shared-model"}' not in metrics_text


def test_chat_completions_rejects_missing_key_when_strict_mode_enabled(
    sample_config_copy,
    write_config,
) -> None:
    from vllm_source_gateway.main import create_app

    sample_config_copy["security"]["reject_unknown_api_keys"] = True
    config_path = write_config(sample_config_copy, filename="strict-missing-key.yaml")

    with TestClient(create_app(config_path=config_path)) as app_client:
        response = app_client.post(
            "/v1/chat/completions",
            json={
                "model": "shared-model",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

        assert response.status_code == 401
        assert response.json() == {"detail": "missing api key"}

        metrics_text = app_client.get("/metrics").text

        assert (
            'gateway_source_resolution_total{department="unknown",resolution_source="unknown"} 1.0'
            in metrics_text
        )
        assert (
            'gateway_http_requests_total{department="unknown",endpoint="chat_completions",method="POST",status_class="4xx"} 1.0'
            in metrics_text
        )
