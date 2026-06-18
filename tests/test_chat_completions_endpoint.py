from __future__ import annotations

import json

import httpx

from conftest import FakeStreamingUpstreamResponse, FakeUpstreamResponse, TestClient


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
    assert recorder["headers"]["authorization"] == "Bearer upstream-token-a"
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
    assert (
        'gateway_http_request_failures_total{department="dept-a",endpoint="chat_completions",failure_origin="gateway",method="POST",status_class="5xx"} 1.0'
        in metrics_text
    )


def test_chat_completions_failsover_to_second_upstream_on_connect_error(
    app_client,
    install_fake_async_client,
) -> None:
    recorder = install_fake_async_client(
        app_client=app_client,
        post_side_effects=[
            httpx.ConnectError("connection failed"),
            FakeUpstreamResponse(
                payload={
                    "id": "chatcmpl-failover",
                    "object": "chat.completion",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}}],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 6},
                }
            ),
        ],
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
    assert response.json()["id"] == "chatcmpl-failover"
    assert [request["url"] for request in recorder["requests"]] == [
        "http://10.0.0.1:8000/v1/chat/completions",
        "http://10.0.0.2:8000/v1/chat/completions",
    ]
    assert recorder["headers"]["authorization"] == "Bearer upstream-token-b"


def test_chat_completions_tracks_upstream_origin_failures(
    app_client,
    install_fake_async_client,
) -> None:
    install_fake_async_client(
        app_client=app_client,
        response=FakeUpstreamResponse(
            status_code=503,
            payload={"error": {"message": "model overloaded"}},
        ),
    )

    response = app_client.post(
        "/v1/chat/completions",
        headers={"x-api-key": "key-dept-a"},
        json={
            "model": "shared-model",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 503

    metrics_text = app_client.get("/metrics").text
    assert (
        'gateway_http_request_failures_total{department="dept-a",endpoint="chat_completions",failure_origin="upstream",method="POST",status_class="5xx"} 1.0'
        in metrics_text
    )


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
    assert recorder["headers"]["authorization"] == "Bearer upstream-token-a"

    metrics_text = app_client.get("/metrics").text

    assert 'gateway_prompt_tokens_total{department="dept-a",model_name="shared-model"} 7.0' in metrics_text
    assert 'gateway_generation_tokens_total{department="dept-a",model_name="shared-model"} 9.0' in metrics_text
    assert (
        'gateway_token_accounting_total{accounting_status="recorded",endpoint="chat_completions"} 1.0'
        in metrics_text
    )


def test_chat_completions_streaming_failsover_to_second_upstream_on_connect_error(
    app_client,
    install_fake_async_client,
) -> None:
    recorder = install_fake_async_client(
        app_client=app_client,
        send_side_effects=[
            httpx.ConnectError("connection failed"),
            FakeStreamingUpstreamResponse(
                chunks=[
                    b'data: {"id":"chunk-1","choices":[{"delta":{"content":"ok"}}]}\n\n',
                    b"data: [DONE]\n\n",
                ]
            ),
        ],
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
    assert b'"content":"ok"' in body
    assert [request["url"] for request in recorder["requests"]] == [
        "http://10.0.0.1:8000/v1/chat/completions",
        "http://10.0.0.2:8000/v1/chat/completions",
    ]
    assert recorder["headers"]["authorization"] == "Bearer upstream-token-b"


def test_chat_completions_unexpected_500_is_recorded_by_metrics_middleware(
    sample_config_path,
    install_fake_async_client,
) -> None:
    from vllm_source_gateway.main import create_app

    with TestClient(
        create_app(config_path=sample_config_path),
        raise_server_exceptions=False,
    ) as app_client:
        install_fake_async_client(
            app_client=app_client,
            exception=RuntimeError("unexpected failure"),
        )

        response = app_client.post(
            "/v1/chat/completions",
            headers={"x-api-key": "key-dept-a"},
            json={
                "model": "shared-model",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

        assert response.status_code == 500

        metrics_text = app_client.get("/metrics").text

        assert (
            'gateway_http_requests_total{department="dept-a",endpoint="chat_completions",method="POST",status_class="5xx"} 1.0'
            in metrics_text
        )


def test_chat_completions_returns_413_when_request_body_exceeds_limit(
    sample_config_copy,
    write_config,
) -> None:
    from vllm_source_gateway.main import create_app

    sample_config_copy["server"]["max_request_body_bytes"] = 64
    config_path = write_config(sample_config_copy, filename="small-body-limit.yaml")
    payload = {
        "model": "shared-model",
        "messages": [{"role": "user", "content": "hello from an oversized payload"}],
    }

    with TestClient(create_app(config_path=config_path)) as app_client:
        response = app_client.post(
            "/v1/chat/completions",
            headers={"x-api-key": "key-dept-a", "content-type": "application/json"},
            content=json.dumps(payload).encode("utf-8"),
        )

        assert response.status_code == 413
        assert response.json() == {
            "detail": "request body too large; max_request_body_bytes=64"
        }

        metrics_text = app_client.get("/metrics").text
        assert (
            'gateway_http_requests_total{department="dept-a",endpoint="chat_completions",method="POST",status_class="4xx"} 1.0'
            in metrics_text
        )
