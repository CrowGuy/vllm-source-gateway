from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FakeStreamingUpstreamResponse, FakeUpstreamResponse, TestClient

from vllm_source_gateway.config import AppConfig
from vllm_source_gateway.metrics import GatewayMetrics
from vllm_source_gateway.services.admission_control import AdmissionController


def _client_with_admission(sample_config_copy, write_config, admission_control: dict) -> TestClient:
    from vllm_source_gateway.main import create_app

    sample_config_copy["admission_control"] = admission_control
    config_path: Path = write_config(sample_config_copy, filename="admission-control.yaml")
    return TestClient(create_app(config_path=config_path))


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _controller(
    sample_config_copy,
    admission_control: dict,
    *,
    time_source=None,
) -> AdmissionController:
    sample_config_copy["admission_control"] = admission_control
    kwargs = {
        "config": AppConfig.model_validate(sample_config_copy),
        "metrics": GatewayMetrics(),
    }
    if time_source is not None:
        kwargs["time_source"] = time_source
    return AdmissionController(**kwargs)


def test_admission_rejections_do_not_open_retry_guard(sample_config_copy) -> None:
    controller = _controller(
        sample_config_copy,
        {
            "enabled": True,
            "global_model_limits": {"shared-model": {"max_active_requests": 1}},
            "retry_guard": {
                "enabled": True,
                "window_seconds": 60,
                "max_events": 2,
                "cooldown_seconds": 20,
            },
        },
    )
    lease = controller.acquire(
        department="dept-a",
        model_name="shared-model",
        endpoint="chat_completions",
    )
    try:
        for _ in range(3):
            with pytest.raises(Exception) as exc_info:
                controller.acquire(
                    department="dept-a",
                    model_name="shared-model",
                    endpoint="chat_completions",
                )
            assert exc_info.value.status_code == 429
            assert exc_info.value.headers["Retry-After"] == "30"
    finally:
        controller.release(lease)

    allowed = controller.acquire(
        department="dept-a",
        model_name="shared-model",
        endpoint="chat_completions",
    )
    controller.release(allowed)


def test_token_budget_rejections_do_not_open_retry_guard(sample_config_copy) -> None:
    controller = _controller(
        sample_config_copy,
        {
            "enabled": True,
            "token_budgets": [
                {
                    "department": "dept-a",
                    "model_name": "shared-model",
                    "max_tokens": 1,
                    "window_seconds": 60,
                }
            ],
            "retry_guard": {
                "enabled": True,
                "window_seconds": 60,
                "max_events": 2,
                "cooldown_seconds": 20,
            },
        },
    )
    controller.record_tokens(department="dept-a", model_name="shared-model", tokens=2)

    for _ in range(3):
        with pytest.raises(Exception) as exc_info:
            controller.acquire(
                department="dept-a",
                model_name="shared-model",
                endpoint="chat_completions",
            )
        assert exc_info.value.status_code == 429
        assert exc_info.value.detail == "token budget exceeded"
        assert exc_info.value.headers["Retry-After"] == "30"

    controller.record_retry_event(department="dept-b", model_name="shared-model")
    allowed = controller.acquire(
        department="dept-b",
        model_name="shared-model",
        endpoint="chat_completions",
    )
    controller.release(allowed)


def test_record_retry_event_opens_retry_guard(sample_config_copy) -> None:
    controller = _controller(
        sample_config_copy,
        {
            "enabled": True,
            "retry_guard": {
                "enabled": True,
                "window_seconds": 60,
                "max_events": 2,
                "cooldown_seconds": 20,
            },
        },
    )

    controller.record_retry_event(department="dept-a", model_name="shared-model")
    controller.record_retry_event(department="dept-a", model_name="shared-model")

    with pytest.raises(Exception) as exc_info:
        controller.acquire(
            department="dept-a",
            model_name="shared-model",
            endpoint="chat_completions",
    )
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "retry guard is open"
    assert "Retry-After" in exc_info.value.headers


def test_token_budget_window_expiry_allows_requests_again(sample_config_copy) -> None:
    clock = FakeClock()
    controller = _controller(
        sample_config_copy,
        {
            "enabled": True,
            "token_budgets": [
                {
                    "department": "dept-a",
                    "model_name": "shared-model",
                    "max_tokens": 10,
                    "window_seconds": 60,
                }
            ],
        },
        time_source=clock,
    )

    controller.record_tokens(department="dept-a", model_name="shared-model", tokens=11)
    clock.advance(30)
    with pytest.raises(Exception) as exc_info:
        controller.acquire(
            department="dept-a",
            model_name="shared-model",
            endpoint="chat_completions",
        )
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "token budget exceeded"

    clock.advance(31)
    lease = controller.acquire(
        department="dept-a",
        model_name="shared-model",
        endpoint="chat_completions",
    )
    controller.release(lease)


def test_retry_guard_cooldown_expiry_allows_requests_again(sample_config_copy) -> None:
    clock = FakeClock()
    controller = _controller(
        sample_config_copy,
        {
            "enabled": True,
            "retry_guard": {
                "enabled": True,
                "window_seconds": 60,
                "max_events": 2,
                "cooldown_seconds": 20,
            },
        },
        time_source=clock,
    )

    controller.record_retry_event(department="dept-a", model_name="shared-model")
    controller.record_retry_event(department="dept-a", model_name="shared-model")
    with pytest.raises(Exception) as exc_info:
        controller.acquire(
            department="dept-a",
            model_name="shared-model",
            endpoint="chat_completions",
        )
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "retry guard is open"

    clock.advance(21)
    lease = controller.acquire(
        department="dept-a",
        model_name="shared-model",
        endpoint="chat_completions",
    )
    controller.release(lease)


def test_retry_guard_requires_full_event_window_after_cooldown(sample_config_copy) -> None:
    clock = FakeClock()
    controller = _controller(
        sample_config_copy,
        {
            "enabled": True,
            "retry_guard": {
                "enabled": True,
                "window_seconds": 60,
                "max_events": 2,
                "cooldown_seconds": 20,
            },
        },
        time_source=clock,
    )

    controller.record_retry_event(department="dept-a", model_name="shared-model")
    controller.record_retry_event(department="dept-a", model_name="shared-model")
    controller.record_retry_event(department="dept-a", model_name="shared-model")
    clock.advance(21)

    controller.record_retry_event(department="dept-a", model_name="shared-model")
    lease = controller.acquire(
        department="dept-a",
        model_name="shared-model",
        endpoint="chat_completions",
    )
    controller.release(lease)

    controller.record_retry_event(department="dept-a", model_name="shared-model")
    with pytest.raises(Exception) as exc_info:
        controller.acquire(
            department="dept-a",
            model_name="shared-model",
            endpoint="chat_completions",
        )
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "retry guard is open"


def test_retry_event_window_expiry_does_not_open_guard(sample_config_copy) -> None:
    clock = FakeClock()
    controller = _controller(
        sample_config_copy,
        {
            "enabled": True,
            "retry_guard": {
                "enabled": True,
                "window_seconds": 60,
                "max_events": 2,
                "cooldown_seconds": 20,
            },
        },
        time_source=clock,
    )

    controller.record_retry_event(department="dept-a", model_name="shared-model")
    clock.advance(61)
    controller.record_retry_event(department="dept-a", model_name="shared-model")

    lease = controller.acquire(
        department="dept-a",
        model_name="shared-model",
        endpoint="chat_completions",
    )
    controller.release(lease)


def test_token_budget_running_total_drops_expired_events(sample_config_copy) -> None:
    clock = FakeClock()
    controller = _controller(
        sample_config_copy,
        {
            "enabled": True,
            "token_budgets": [
                {
                    "department": "dept-a",
                    "model_name": "shared-model",
                    "max_tokens": 10,
                    "window_seconds": 60,
                }
            ],
        },
        time_source=clock,
    )

    controller.record_tokens(department="dept-a", model_name="shared-model", tokens=6)
    clock.advance(10)
    controller.record_tokens(department="dept-a", model_name="shared-model", tokens=6)
    with pytest.raises(Exception) as exc_info:
        controller.acquire(
            department="dept-a",
            model_name="shared-model",
            endpoint="chat_completions",
        )
    assert exc_info.value.detail == "token budget exceeded"

    clock.advance(51)
    lease = controller.acquire(
        department="dept-a",
        model_name="shared-model",
        endpoint="chat_completions",
    )
    controller.release(lease)


def test_request_shape_limit_injects_chat_output_cap(sample_config_copy) -> None:
    controller = _controller(
        sample_config_copy,
        {
            "enabled": True,
            "request_shape_limits": {"shared-model": {"max_output_tokens": 4096}},
        },
    )

    payload = controller.check_request_shape(
        department="dept-a",
        model_name="shared-model",
        endpoint="chat_completions",
        body_size=100,
        payload={"model": "shared-model", "messages": []},
    )

    assert payload["max_tokens"] == 4096
    assert "max_output_tokens" not in payload


def test_request_shape_limit_injects_responses_output_cap(sample_config_copy) -> None:
    controller = _controller(
        sample_config_copy,
        {
            "enabled": True,
            "request_shape_limits": {"shared-model": {"max_output_tokens": 4096}},
        },
    )

    payload = controller.check_request_shape(
        department="dept-a",
        model_name="shared-model",
        endpoint="responses",
        body_size=100,
        payload={"model": "shared-model", "input": "hi"},
    )

    assert payload["max_output_tokens"] == 4096
    assert "max_tokens" not in payload


def test_request_shape_limit_injects_messages_output_cap(sample_config_copy) -> None:
    controller = _controller(
        sample_config_copy,
        {
            "enabled": True,
            "request_shape_limits": {"shared-model": {"max_output_tokens": 4096}},
        },
    )

    payload = controller.check_request_shape(
        department="dept-a",
        model_name="shared-model",
        endpoint="messages",
        body_size=100,
        payload={"model": "shared-model", "messages": []},
    )

    assert payload["max_tokens"] == 4096
    assert "max_output_tokens" not in payload


def test_request_shape_limit_keeps_existing_output_cap(sample_config_copy) -> None:
    controller = _controller(
        sample_config_copy,
        {
            "enabled": True,
            "request_shape_limits": {"shared-model": {"max_output_tokens": 4096}},
        },
    )

    payload = controller.check_request_shape(
        department="dept-a",
        model_name="shared-model",
        endpoint="chat_completions",
        body_size=100,
        payload={"model": "shared-model", "messages": [], "max_tokens": 128},
    )

    assert payload["max_tokens"] == 128
    assert "max_output_tokens" not in payload


def test_request_shape_limit_rejects_existing_output_cap_above_limit(sample_config_copy) -> None:
    controller = _controller(
        sample_config_copy,
        {
            "enabled": True,
            "request_shape_limits": {"shared-model": {"max_output_tokens": 4096}},
        },
    )

    with pytest.raises(Exception) as exc_info:
        controller.check_request_shape(
            department="dept-a",
            model_name="shared-model",
            endpoint="chat_completions",
            body_size=100,
            payload={"model": "shared-model", "messages": [], "max_tokens": 4097},
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "max output tokens limit exceeded or invalid"
    assert "Retry-After" not in (exc_info.value.headers or {})


@pytest.mark.parametrize(
    "payload",
    [
        {"model": "shared-model", "messages": [], "max_tokens": None},
        {"model": "shared-model", "messages": [], "max_tokens": 999999.0},
        {"model": "shared-model", "messages": [], "max_output_tokens": "999999"},
        {"model": "shared-model", "messages": [], "max_tokens": True},
    ],
)
def test_request_shape_limit_rejects_invalid_output_cap_values(
    sample_config_copy,
    payload,
) -> None:
    controller = _controller(
        sample_config_copy,
        {
            "enabled": True,
            "request_shape_limits": {"shared-model": {"max_output_tokens": 4096}},
        },
    )

    with pytest.raises(Exception) as exc_info:
        controller.check_request_shape(
            department="dept-a",
            model_name="shared-model",
            endpoint="chat_completions",
            body_size=100,
            payload=payload,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "max output tokens limit exceeded or invalid"
    assert "Retry-After" not in (exc_info.value.headers or {})


def test_request_shape_limit_rejects_body_too_large_with_413(sample_config_copy) -> None:
    controller = _controller(
        sample_config_copy,
        {
            "enabled": True,
            "request_shape_limits": {
                "shared-model": {
                    "max_request_body_bytes": 4,
                }
            },
        },
    )

    with pytest.raises(Exception) as exc_info:
        controller.check_request_shape(
            department="dept-a",
            model_name="shared-model",
            endpoint="chat_completions",
            body_size=5,
            payload={"model": "shared-model", "messages": []},
        )

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == "request body too large"
    assert "Retry-After" not in (exc_info.value.headers or {})


def test_request_shape_limit_rejects_n_greater_than_one_with_422(sample_config_copy) -> None:
    controller = _controller(
        sample_config_copy,
        {
            "enabled": True,
            "request_shape_limits": {"shared-model": {"reject_n_greater_than_one": True}},
        },
    )

    with pytest.raises(Exception) as exc_info:
        controller.check_request_shape(
            department="dept-a",
            model_name="shared-model",
            endpoint="chat_completions",
            body_size=100,
            payload={"model": "shared-model", "messages": [], "n": 2},
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "n greater than one is not allowed"
    assert "Retry-After" not in (exc_info.value.headers or {})


def test_model_concurrency_limit_returns_429(
    sample_config_copy,
    write_config,
    install_fake_async_client,
) -> None:
    with _client_with_admission(
        sample_config_copy,
        write_config,
        {
            "enabled": True,
            "default_retry_after_seconds": 9,
            "global_model_limits": {"shared-model": {"max_active_requests": 1}},
        },
    ) as app_client:
        install_fake_async_client(app_client=app_client)
        controller = app_client.app.state.admission_controller
        lease = controller.acquire(
            department="dept-b",
            model_name="shared-model",
            endpoint="chat_completions",
        )
        try:
            response = app_client.post(
                "/v1/chat/completions",
                headers={"x-api-key": "key-dept-a"},
                json={"model": "shared-model", "messages": [{"role": "user", "content": "hi"}]},
            )
        finally:
            controller.release(lease)

        assert response.status_code == 429
        assert response.headers["retry-after"] == "9"
        assert response.json() == {"detail": "model concurrency limit exceeded"}
        expected_metric = (
            'gateway_admission_rejections_total{department="dept-a",'
            'model_name="shared-model",reason="model_concurrency"} 1.0'
        )
        assert expected_metric in app_client.get("/metrics").text


def test_department_model_concurrency_limit_is_department_scoped(
    sample_config_copy,
    write_config,
    install_fake_async_client,
) -> None:
    with _client_with_admission(
        sample_config_copy,
        write_config,
        {
            "enabled": True,
            "department_model_limits": [
                {
                    "department": "dept-a",
                    "model_name": "shared-model",
                    "max_active_requests": 1,
                }
            ],
        },
    ) as app_client:
        install_fake_async_client(
            app_client=app_client,
            response=FakeUpstreamResponse(
                payload={"id": "ok", "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
            ),
        )
        controller = app_client.app.state.admission_controller
        lease = controller.acquire(
            department="dept-a",
            model_name="shared-model",
            endpoint="chat_completions",
        )
        try:
            rejected = app_client.post(
                "/v1/chat/completions",
                headers={"x-api-key": "key-dept-a"},
                json={"model": "shared-model", "messages": [{"role": "user", "content": "hi"}]},
            )
            accepted = app_client.post(
                "/v1/chat/completions",
                headers={"x-api-key": "key-dept-b"},
                json={"model": "shared-model", "messages": [{"role": "user", "content": "hi"}]},
            )
        finally:
            controller.release(lease)

        assert rejected.status_code == 429
        assert accepted.status_code == 200


def test_token_budget_rejects_after_reliable_usage(
    sample_config_copy,
    write_config,
    install_fake_async_client,
) -> None:
    with _client_with_admission(
        sample_config_copy,
        write_config,
        {
            "enabled": True,
            "token_budgets": [
                {
                    "department": "dept-a",
                    "model_name": "shared-model",
                    "max_tokens": 10,
                    "window_seconds": 60,
                }
            ],
        },
    ) as app_client:
        recorder = install_fake_async_client(
            app_client=app_client,
            response=FakeUpstreamResponse(
                payload={"id": "ok", "usage": {"prompt_tokens": 7, "completion_tokens": 5}}
            ),
        )

        first = app_client.post(
            "/v1/chat/completions",
            headers={"x-api-key": "key-dept-a"},
            json={"model": "shared-model", "messages": [{"role": "user", "content": "hi"}]},
        )
        second = app_client.post(
            "/v1/chat/completions",
            headers={"x-api-key": "key-dept-a"},
            json={"model": "shared-model", "messages": [{"role": "user", "content": "hi"}]},
        )

        assert first.status_code == 200
        assert second.status_code == 429
        assert second.json() == {"detail": "token budget exceeded"}
        assert len(recorder["requests"]) == 1
        metrics_text = app_client.get("/metrics").text
        expected_metric = (
            'gateway_token_budget_rejections_total{department="dept-a",'
            'model_name="shared-model"} 1.0'
        )
        assert expected_metric in metrics_text


def test_token_budget_ignores_missing_usage(
    sample_config_copy,
    write_config,
    install_fake_async_client,
) -> None:
    with _client_with_admission(
        sample_config_copy,
        write_config,
        {
            "enabled": True,
            "token_budgets": [
                {
                    "department": "dept-a",
                    "model_name": "shared-model",
                    "max_tokens": 10,
                    "window_seconds": 60,
                }
            ],
        },
    ) as app_client:
        recorder = install_fake_async_client(
            app_client=app_client,
            post_side_effects=[
                FakeUpstreamResponse(payload={"id": "missing-usage"}),
                FakeUpstreamResponse(payload={"id": "still-allowed"}),
            ],
        )

        first = app_client.post(
            "/v1/chat/completions",
            headers={"x-api-key": "key-dept-a"},
            json={"model": "shared-model", "messages": [{"role": "user", "content": "hi"}]},
        )
        second = app_client.post(
            "/v1/chat/completions",
            headers={"x-api-key": "key-dept-a"},
            json={"model": "shared-model", "messages": [{"role": "user", "content": "hi"}]},
        )

        assert first.status_code == 200
        assert second.status_code == 200
        assert len(recorder["requests"]) == 2


def test_request_shape_limit_rejects_max_output_tokens(
    sample_config_copy,
    write_config,
    install_fake_async_client,
) -> None:
    with _client_with_admission(
        sample_config_copy,
        write_config,
        {
            "enabled": True,
            "request_shape_limits": {
                "shared-model": {
                    "max_output_tokens": 4,
                }
            },
        },
    ) as app_client:
        recorder = install_fake_async_client(app_client=app_client)

        response = app_client.post(
            "/v1/chat/completions",
            headers={"x-api-key": "key-dept-a"},
            json={
                "model": "shared-model",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 5,
            },
        )

        assert response.status_code == 422
        assert "retry-after" not in response.headers
        assert recorder["requests"] == []
        expected_metric = (
            'gateway_admission_rejections_total{department="dept-a",'
            'model_name="shared-model",reason="max_output_tokens"} 1.0'
        )
        assert expected_metric in app_client.get("/metrics").text


def test_retry_guard_cooldown_after_repeated_upstream_5xx(
    sample_config_copy,
    write_config,
    install_fake_async_client,
) -> None:
    with _client_with_admission(
        sample_config_copy,
        write_config,
        {
            "enabled": True,
            "retry_guard": {
                "enabled": True,
                "window_seconds": 60,
                "max_events": 2,
                "cooldown_seconds": 20,
            },
        },
    ) as app_client:
        recorder = install_fake_async_client(
            app_client=app_client,
            post_side_effects=[
                FakeUpstreamResponse(status_code=503, payload={"error": "overloaded"}),
                FakeUpstreamResponse(status_code=503, payload={"error": "overloaded"}),
                FakeUpstreamResponse(payload={"id": "should-not-run"}),
            ],
        )

        first = app_client.post(
            "/v1/chat/completions",
            headers={"x-api-key": "key-dept-a"},
            json={"model": "shared-model", "messages": [{"role": "user", "content": "hi"}]},
        )
        second = app_client.post(
            "/v1/chat/completions",
            headers={"x-api-key": "key-dept-a"},
            json={"model": "shared-model", "messages": [{"role": "user", "content": "hi"}]},
        )
        third = app_client.post(
            "/v1/chat/completions",
            headers={"x-api-key": "key-dept-a"},
            json={"model": "shared-model", "messages": [{"role": "user", "content": "hi"}]},
        )

        assert first.status_code == 503
        assert second.status_code == 503
        assert third.status_code == 429
        assert third.headers["retry-after"] in {"19", "20"}
        assert len(recorder["requests"]) == 2
        assert (
            'gateway_retry_guard_open_total{department="dept-a",model_name="shared-model"} 1.0'
            in app_client.get("/metrics").text
        )


def test_streaming_request_releases_inflight_slot(
    sample_config_copy,
    write_config,
    install_fake_async_client,
) -> None:
    with _client_with_admission(
        sample_config_copy,
        write_config,
        {
            "enabled": True,
            "global_model_limits": {"shared-model": {"max_active_requests": 1}},
        },
    ) as app_client:
        install_fake_async_client(
            app_client=app_client,
            stream_response=FakeStreamingUpstreamResponse(
                chunks=[
                    b'data: {"id":"chunk","usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n',
                    b"data: [DONE]\n\n",
                ],
            ),
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
        assert b"chunk" in body
        metrics_text = app_client.get("/metrics").text
        expected_metric = (
            'gateway_inflight_requests{department="dept-a",endpoint="chat_completions",'
            'model_name="shared-model"} 0.0'
        )
        assert expected_metric in metrics_text
