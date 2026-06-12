from __future__ import annotations

import httpx

from tests.conftest import FakeUpstreamResponse


def test_chat_completions_records_parse_error_and_returns_raw_body(
    app_client,
    install_fake_async_client,
) -> None:
    install_fake_async_client(
        app_client=app_client,
        response=FakeUpstreamResponse(
            status_code=200,
            content=b"not-json",
            headers={"content-type": "text/plain"},
            json_error=ValueError("invalid json"),
        )
    )

    response = app_client.post(
        "/v1/chat/completions",
        headers={"x-api-key": "key-dept-a"},
        json={
            "model": "shared-model",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 200
    assert response.text == "not-json"
    assert response.headers["content-type"].startswith("text/plain")

    metrics_text = app_client.get("/metrics").text

    assert (
        'gateway_token_accounting_total{accounting_status="parse_error",endpoint="chat_completions"} 1.0'
        in metrics_text
    )
    assert 'gateway_prompt_tokens_total{department="dept-a",model_name="shared-model"}' not in metrics_text
    assert 'gateway_generation_tokens_total{department="dept-a",model_name="shared-model"}' not in metrics_text


def test_responses_timeout_records_missing_usage_without_token_counters(
    app_client,
    install_fake_async_client,
) -> None:
    install_fake_async_client(app_client=app_client, exception=httpx.TimeoutException("timed out"))

    response = app_client.post(
        "/v1/responses",
        headers={"x-api-key": "key-dept-b"},
        json={
            "model": "model-b",
            "input": "hello",
        },
    )

    assert response.status_code == 504
    assert response.json() == {"detail": "upstream request timed out"}

    metrics_text = app_client.get("/metrics").text

    assert (
        'gateway_token_accounting_total{accounting_status="missing_usage",endpoint="responses"} 1.0'
        in metrics_text
    )
    assert 'gateway_prompt_tokens_total{department="dept-b",model_name="model-b"}' not in metrics_text
    assert 'gateway_generation_tokens_total{department="dept-b",model_name="model-b"}' not in metrics_text


def test_chat_completions_zero_usage_records_accounting_without_incrementing_token_counters(
    app_client,
    install_fake_async_client,
) -> None:
    install_fake_async_client(
        app_client=app_client,
        response=FakeUpstreamResponse(
            payload={
                "id": "chatcmpl-zero",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": ""}}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            }
        )
    )

    response = app_client.post(
        "/v1/chat/completions",
        headers={"x-api-key": "key-dept-a"},
        json={
            "model": "shared-model",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 200

    metrics_text = app_client.get("/metrics").text

    assert (
        'gateway_token_accounting_total{accounting_status="recorded",endpoint="chat_completions"} 1.0'
        in metrics_text
    )
    assert 'gateway_prompt_tokens_total{department="dept-a",model_name="shared-model"}' not in metrics_text
    assert 'gateway_generation_tokens_total{department="dept-a",model_name="shared-model"}' not in metrics_text
