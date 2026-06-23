from __future__ import annotations

import json

import httpx

from conftest import FakeStreamingUpstreamResponse, FakeUpstreamResponse, TestClient


def test_messages_proxies_success_and_records_usage(
    app_client,
    install_fake_async_client,
) -> None:
    recorder = install_fake_async_client(
        app_client=app_client,
        response=FakeUpstreamResponse(
            headers={
                "content-type": "application/json",
                "connection": "keep-alive",
                "transfer-encoding": "chunked",
                "content-encoding": "gzip",
            },
            payload={
                "id": "msg_123",
                "type": "message",
                "role": "assistant",
                "model": "shared-model",
                "content": [{"type": "text", "text": "hello from upstream"}],
                "usage": {"input_tokens": 12, "output_tokens": 34},
            },
        ),
    )

    response = app_client.post(
        "/v1/messages",
        headers={
            "x-api-key": "key-dept-a",
            "authorization": "Bearer caller-token",
            "anthropic-version": "2023-06-01",
            "cookie": "session=abc",
            "accept-encoding": "gzip",
            "connection": "keep-alive",
            "x-trace-id": "trace-123",
            "x-request-id": "req-123",
        },
        json={
            "model": "shared-model",
            "max_tokens": 128,
            "tools": [{"name": "read_file", "input_schema": {"type": "object"}}],
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == "msg_123"
    assert response.json()["usage"] == {"input_tokens": 12, "output_tokens": 34}
    assert recorder["url"] == "http://10.0.0.1:8000/v1/messages"
    assert recorder["json"] == {
        "model": "shared-model",
        "max_tokens": 128,
        "tools": [{"name": "read_file", "input_schema": {"type": "object"}}],
        "messages": [{"role": "user", "content": "hello"}],
    }
    assert recorder["headers"]["authorization"] == "Bearer upstream-token-a"
    assert recorder["headers"]["x-trace-id"] == "trace-123"
    assert recorder["headers"]["x-request-id"] == "req-123"
    assert "x-api-key" not in recorder["headers"]
    assert "cookie" not in recorder["headers"]
    assert "accept-encoding" not in recorder["headers"]
    assert "connection" not in recorder["headers"]
    assert "anthropic-version" not in recorder["headers"]
    assert response.headers["content-type"].startswith("application/json")
    assert "connection" not in response.headers
    assert "transfer-encoding" not in response.headers
    assert "content-encoding" not in response.headers

    metrics_text = app_client.get("/metrics").text
    assert 'gateway_prompt_tokens_total{department="dept-a",model_name="shared-model"} 12.0' in metrics_text
    assert 'gateway_generation_tokens_total{department="dept-a",model_name="shared-model"} 34.0' in metrics_text
    assert 'gateway_http_requests_total{department="dept-a",endpoint="messages",method="POST",status_class="2xx"} 1.0' in metrics_text
    assert 'gateway_token_accounting_total{accounting_status="recorded",endpoint="messages"} 1.0' in metrics_text


def test_messages_resolves_department_from_bearer_api_key(
    app_client,
    install_fake_async_client,
) -> None:
    install_fake_async_client(
        app_client=app_client,
        response=FakeUpstreamResponse(
            payload={
                "id": "msg-bearer",
                "type": "message",
                "role": "assistant",
                "model": "shared-model",
                "content": [{"type": "text", "text": "hello"}],
                "usage": {"input_tokens": 5, "output_tokens": 8},
            },
        ),
    )

    response = app_client.post(
        "/v1/messages",
        headers={"authorization": "Bearer key-dept-a"},
        json={
            "model": "shared-model",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 200

    metrics_text = app_client.get("/metrics").text
    assert 'gateway_source_resolution_total{department="dept-a",resolution_source="api_key"} 1.0' in metrics_text
    assert 'gateway_http_requests_total{department="dept-a",endpoint="messages",method="POST",status_class="2xx"} 1.0' in metrics_text


def test_messages_tracks_missing_usage_without_guessing_tokens(
    app_client,
    install_fake_async_client,
) -> None:
    install_fake_async_client(
        app_client=app_client,
        response=FakeUpstreamResponse(
            payload={
                "id": "msg-usage-missing",
                "type": "message",
                "role": "assistant",
                "model": "shared-model",
                "content": [{"type": "text", "text": "hello"}],
            },
        ),
    )

    response = app_client.post(
        "/v1/messages",
        headers={"x-api-key": "key-dept-a"},
        json={
            "model": "shared-model",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 200

    metrics_text = app_client.get("/metrics").text
    assert 'gateway_token_accounting_total{accounting_status="missing_usage",endpoint="messages"} 1.0' in metrics_text
    assert 'gateway_prompt_tokens_total{department="dept-a",model_name="shared-model"}' not in metrics_text
    assert 'gateway_generation_tokens_total{department="dept-a",model_name="shared-model"}' not in metrics_text


def test_messages_returns_raw_upstream_error_body(
    app_client,
    install_fake_async_client,
) -> None:
    install_fake_async_client(
        app_client=app_client,
        response=FakeUpstreamResponse(
            status_code=400,
            payload={
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "tool schema is invalid",
                },
            },
        ),
    )

    response = app_client.post(
        "/v1/messages",
        headers={"x-api-key": "key-dept-a"},
        json={
            "model": "shared-model",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [{"name": "bad_tool"}],
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "tool schema is invalid",
        },
    }

    metrics_text = app_client.get("/metrics").text
    assert (
        'gateway_http_request_failures_total{department="dept-a",endpoint="messages",failure_origin="upstream",method="POST",status_class="4xx"} 1.0'
        in metrics_text
    )


def test_messages_returns_422_when_model_is_missing(app_client) -> None:
    response = app_client.post(
        "/v1/messages",
        headers={"x-api-key": "key-dept-a"},
        json={
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "missing model"}

    metrics_text = app_client.get("/metrics").text
    assert (
        'gateway_http_request_failures_total{department="dept-a",endpoint="messages",failure_origin="gateway",method="POST",status_class="4xx"} 1.0'
        in metrics_text
    )


def test_messages_streams_sse_and_tracks_usage(
    app_client,
    install_fake_async_client,
) -> None:
    recorder = install_fake_async_client(
        app_client=app_client,
        stream_response=FakeStreamingUpstreamResponse(
            chunks=[
                b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","usage":{"input_tokens":7,"output_tokens":0}}}\n\n',
                b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hel"}}\n\n',
                b'event: message_delta\ndata: {"type":"message_delta","usage":{"input_tokens":7,"output_tokens":9}}\n\n',
                b"event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n",
            ],
        ),
    )

    with app_client.stream(
        "POST",
        "/v1/messages",
        headers={"x-api-key": "key-dept-a", "anthropic-version": "2023-06-01"},
        json={
            "model": "shared-model",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
            "tools": [{"name": "read_file", "input_schema": {"type": "object"}}],
        },
    ) as response:
        body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert b"event: message_start" in body
    assert b"event: content_block_delta" in body
    assert b'"input_tokens":7' in body
    assert b'"output_tokens":9' in body
    assert recorder["send_stream"] is True
    assert recorder["url"] == "http://10.0.0.1:8000/v1/messages"
    assert recorder["headers"]["authorization"] == "Bearer upstream-token-a"

    metrics_text = app_client.get("/metrics").text
    assert 'gateway_prompt_tokens_total{department="dept-a",model_name="shared-model"} 7.0' in metrics_text
    assert 'gateway_generation_tokens_total{department="dept-a",model_name="shared-model"} 9.0' in metrics_text
    assert 'gateway_token_accounting_total{accounting_status="recorded",endpoint="messages"} 1.0' in metrics_text


def test_messages_streaming_failsover_to_second_upstream_on_connect_error(
    app_client,
    install_fake_async_client,
) -> None:
    recorder = install_fake_async_client(
        app_client=app_client,
        send_side_effects=[
            httpx.ConnectError("connection failed"),
            FakeStreamingUpstreamResponse(
                chunks=[
                    b'event: content_block_delta\ndata: {"delta":{"text":"ok"}}\n\n',
                    b"event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n",
                ],
            ),
        ],
    )

    with app_client.stream(
        "POST",
        "/v1/messages",
        headers={"x-api-key": "key-dept-a"},
        json={
            "model": "shared-model",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    ) as response:
        body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert b'"text":"ok"' in body
    assert [request["url"] for request in recorder["requests"]] == [
        "http://10.0.0.1:8000/v1/messages",
        "http://10.0.0.2:8000/v1/messages",
    ]
    assert recorder["headers"]["authorization"] == "Bearer upstream-token-b"


def test_messages_returns_504_on_upstream_timeout(
    app_client,
    install_fake_async_client,
) -> None:
    install_fake_async_client(
        app_client=app_client,
        exception=httpx.TimeoutException("timed out"),
    )

    response = app_client.post(
        "/v1/messages",
        headers={"x-api-key": "key-dept-a"},
        json={
            "model": "shared-model",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 504
    assert response.json() == {"detail": "upstream request timed out"}

    metrics_text = app_client.get("/metrics").text
    assert 'gateway_token_accounting_total{accounting_status="missing_usage",endpoint="messages"} 1.0' in metrics_text
    assert 'gateway_http_requests_total{department="dept-a",endpoint="messages",method="POST",status_class="5xx"} 1.0' in metrics_text
    assert (
        'gateway_http_request_failures_total{department="dept-a",endpoint="messages",failure_origin="gateway",method="POST",status_class="5xx"} 1.0'
        in metrics_text
    )


def test_messages_returns_413_when_request_body_exceeds_limit(
    sample_config_copy,
    write_config,
) -> None:
    from vllm_source_gateway.main import create_app

    sample_config_copy["server"]["max_request_body_bytes"] = 64
    config_path = write_config(sample_config_copy, filename="small-messages-body-limit.yaml")
    payload = {
        "model": "shared-model",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "hello from an oversized payload"}],
    }

    with TestClient(create_app(config_path=config_path)) as app_client:
        response = app_client.post(
            "/v1/messages",
            headers={"x-api-key": "key-dept-a", "content-type": "application/json"},
            content=json.dumps(payload).encode("utf-8"),
        )

        assert response.status_code == 413
        assert response.json() == {
            "detail": "request body too large; max_request_body_bytes=64"
        }

        metrics_text = app_client.get("/metrics").text
        assert (
            'gateway_http_requests_total{department="dept-a",endpoint="messages",method="POST",status_class="4xx"} 1.0'
            in metrics_text
        )
