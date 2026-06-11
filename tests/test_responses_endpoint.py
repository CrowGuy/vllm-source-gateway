from __future__ import annotations

import httpx

from tests.conftest import FakeUpstreamResponse


def test_responses_proxies_success_and_records_usage(
    app_client,
    install_fake_async_client,
) -> None:
    recorder = install_fake_async_client(
        response=FakeUpstreamResponse(
            payload={
                "id": "resp-123",
                "object": "response",
                "output": [{"type": "message", "role": "assistant"}],
                "usage": {"input_tokens": 8, "output_tokens": 21},
            }
        )
    )

    response = app_client.post(
        "/v1/responses",
        headers={"x-api-key": "key-dept-b"},
        json={
            "model": "model-b",
            "input": "hello",
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == "resp-123"
    assert recorder["url"] == "http://10.0.0.2:8000/v1/responses"
    assert recorder["json"]["model"] == "model-b"

    metrics_response = app_client.get("/metrics")
    metrics_text = metrics_response.text

    assert 'gateway_prompt_tokens_total{department="dept-b",model_name="model-b"} 8.0' in metrics_text
    assert 'gateway_generation_tokens_total{department="dept-b",model_name="model-b"} 21.0' in metrics_text
    assert 'gateway_token_accounting_total{accounting_status="recorded",endpoint="responses"} 1.0' in metrics_text


def test_responses_tracks_missing_usage_without_guessing_tokens(
    app_client,
    install_fake_async_client,
) -> None:
    install_fake_async_client(
        response=FakeUpstreamResponse(
            payload={
                "id": "resp-usage-missing",
                "object": "response",
                "output": [{"type": "message", "role": "assistant"}],
            }
        )
    )

    response = app_client.post(
        "/v1/responses",
        headers={"x-api-key": "key-dept-b"},
        json={
            "model": "model-b",
            "input": "hello",
        },
    )

    assert response.status_code == 200

    metrics_response = app_client.get("/metrics")
    metrics_text = metrics_response.text

    assert 'gateway_token_accounting_total{accounting_status="missing_usage",endpoint="responses"} 1.0' in metrics_text
    assert 'gateway_prompt_tokens_total{department="dept-b",model_name="model-b"}' not in metrics_text
    assert 'gateway_generation_tokens_total{department="dept-b",model_name="model-b"}' not in metrics_text


def test_responses_returns_502_on_upstream_http_error(
    app_client,
    install_fake_async_client,
) -> None:
    install_fake_async_client(exception=httpx.ConnectError("connection failed"))

    response = app_client.post(
        "/v1/responses",
        headers={"x-api-key": "key-dept-b"},
        json={
            "model": "model-b",
            "input": "hello",
        },
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "upstream request failed"}


def test_responses_returns_422_when_model_is_missing(app_client) -> None:
    response = app_client.post(
        "/v1/responses",
        headers={"x-api-key": "key-dept-b"},
        json={"input": "hello"},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "missing model"}
