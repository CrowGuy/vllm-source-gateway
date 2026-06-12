from __future__ import annotations

import httpx

from conftest import FakeStreamingUpstreamResponse, FakeUpstreamResponse


def test_chat_completions_proxies_success_and_records_usage(
    app_client,
    install_fake_async_client,
) -> None:
    recorder = install_fake_async_client(
        app_client=app_client,
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
            "cookie": "session=abc",
            "accept-encoding": "gzip",
            "connection": "keep-alive",
            "x-trace-id": "trace-123",
            "x-request-id": "req-123",
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
    assert recorder["headers"]["x-request-id"] == "req-123"
    assert "x-api-key" not in recorder["headers"]
    assert "authorization" not in recorder["headers"]
    assert "cookie" not in recorder["headers"]
    assert "accept-encoding" not in recorder["headers"]
    assert "connection" not in recorder["headers"]

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
    install_fake_async_client(app_client=app_client, exception=httpx.TimeoutException("timed out"))

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


def test_chat_completions_streams_sse_and_records_usage(
    app_client,
    install_fake_async_client,
) -> None:
    recorder = install_fake_async_client(
        app_client=app_client,
        stream_response=FakeStreamingUpstreamResponse(
            chunks=[
                b'data: {"id":"chunk-1","choices":[{"delta":{"content":"hel"}}]}\n\n',
                b'data: {"id":"chunk-2","choices":[{"delta":{"content":"lo"}}],"usage":{"prompt_tokens":7,"completion_tokens":9}}\n\n',
                b"data: [DONE]\n\n",
            ]
        )
    )

    with app_client.stream(
        "POST",
        "/v1/chat/completions",
        headers={"x-api-key": "key-dept-a"},
        json={
            "model": "shared-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    ) as response:
        body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert b'"prompt_tokens":7' in body
    assert recorder["send_stream"] is True
    assert recorder["url"] == "http://10.0.0.1:8000/v1/chat/completions"

    metrics_text = app_client.get("/metrics").text

    assert 'gateway_prompt_tokens_total{department="dept-a",model_name="shared-model"} 7.0' in metrics_text
    assert 'gateway_generation_tokens_total{department="dept-a",model_name="shared-model"} 9.0' in metrics_text
    assert (
        'gateway_token_accounting_total{accounting_status="recorded",endpoint="chat_completions"} 1.0'
        in metrics_text
    )
