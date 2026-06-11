from __future__ import annotations

import httpx

from tests.conftest import FakeUpstreamResponse


def test_chat_completions_proxies_success_and_records_usage(
    app_client,
    install_fake_async_client,
) -> None:
    recorder = install_fake_async_client(
        response=FakeUpstreamResponse(
            payload={
                "id": "chatcmpl-123",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 34},
            }
        )
    )

    response = app_client.post(
        "/v1/chat/completions",
        headers={
            "x-api-key": "key-dept-a",
            "authorization": "Bearer user-token",
            "x-trace-id": "trace-123",
        },
        json={
            "model": "shared-model",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == "chatcmpl-123"
    assert recorder["url"] == "http://10.0.0.1:8000/v1/chat/completions"
    assert recorder["json"]["model"] == "shared-model"
    assert recorder["headers"]["x-trace-id"] == "trace-123"
    assert "x-api-key" not in recorder["headers"]
    assert "authorization" not in recorder["headers"]

    metrics_response = app_client.get("/metrics")
    metrics_text = metrics_response.text

    assert 'gateway_prompt_tokens_total{department="dept-a",model_name="shared-model"} 12.0' in metrics_text
    assert (
        'gateway_generation_tokens_total{department="dept-a",model_name="shared-model"} 34.0'
        in metrics_text
    )
    assert (
        'gateway_token_accounting_total{accounting_status="recorded",endpoint="chat_completions"} 1.0'
        in metrics_text
    )
    assert (
        'gateway_source_resolution_total{department="dept-a",resolution_source="api_key"} 1.0'
        in metrics_text
    )


def test_chat_completions_returns_404_for_unknown_model(app_client) -> None:
    response = app_client.post(
        "/v1/chat/completions",
        headers={"x-api-key": "key-dept-a"},
        json={
            "model": "missing-model",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "unknown model 'missing-model'"}


def test_chat_completions_returns_504_on_upstream_timeout(
    app_client,
    install_fake_async_client,
) -> None:
    install_fake_async_client(exception=httpx.TimeoutException("timed out"))

    response = app_client.post(
        "/v1/chat/completions",
        headers={"x-api-key": "key-dept-a"},
        json={
            "model": "shared-model",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 504
    assert response.json() == {"detail": "upstream request timed out"}

    metrics_response = app_client.get("/metrics")
    metrics_text = metrics_response.text

    assert (
        'gateway_token_accounting_total{accounting_status="missing_usage",endpoint="chat_completions"} 1.0'
        in metrics_text
    )
    assert 'gateway_http_requests_total{department="dept-a",endpoint="chat_completions",method="POST",status_class="5xx"} 1.0' in metrics_text


def test_chat_completions_rejects_streaming_until_implemented(app_client) -> None:
    response = app_client.post(
        "/v1/chat/completions",
        headers={"x-api-key": "key-dept-a"},
        json={
            "model": "shared-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )

    assert response.status_code == 501
    assert response.json() == {"detail": "streaming not implemented yet"}
