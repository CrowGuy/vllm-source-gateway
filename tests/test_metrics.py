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
