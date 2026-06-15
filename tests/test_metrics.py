from __future__ import annotations

from prometheus_client import generate_latest

from vllm_source_gateway.metrics import GatewayMetrics


def test_request_duration_histogram_uses_llm_latency_buckets() -> None:
    metrics = GatewayMetrics()

    metrics.observe_request(
        department="dept-a",
        endpoint="chat_completions",
        method="POST",
        status_code=200,
        duration_seconds=11.0,
    )

    metrics_text = generate_latest(metrics.registry).decode("utf-8")

    assert (
        'gateway_request_duration_seconds_bucket{department="dept-a",endpoint="chat_completions",le="10.0",method="POST"} 0.0'
        in metrics_text
    )
    assert (
        'gateway_request_duration_seconds_bucket{department="dept-a",endpoint="chat_completions",le="20.0",method="POST"} 1.0'
        in metrics_text
    )
    assert (
        'gateway_request_duration_seconds_bucket{department="dept-a",endpoint="chat_completions",le="120.0",method="POST"} 1.0'
        in metrics_text
    )


def test_request_failure_metric_distinguishes_failure_origin() -> None:
    metrics = GatewayMetrics()

    metrics.record_request_failure(
        department="dept-a",
        endpoint="chat_completions",
        method="POST",
        status_code=504,
        failure_origin="gateway",
    )
    metrics.record_request_failure(
        department="dept-a",
        endpoint="chat_completions",
        method="POST",
        status_code=503,
        failure_origin="upstream",
    )

    metrics_text = generate_latest(metrics.registry).decode("utf-8")

    assert (
        'gateway_http_request_failures_total{department="dept-a",endpoint="chat_completions",failure_origin="gateway",method="POST",status_class="5xx"} 1.0'
        in metrics_text
    )
    assert (
        'gateway_http_request_failures_total{department="dept-a",endpoint="chat_completions",failure_origin="upstream",method="POST",status_class="5xx"} 1.0'
        in metrics_text
    )
