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
        'gateway_request_duration_seconds_bucket{department="dept-a",'
        'endpoint="chat_completions",le="10.0",method="POST"} 0.0'
        in metrics_text
    )
    assert (
        'gateway_request_duration_seconds_bucket{department="dept-a",'
        'endpoint="chat_completions",le="20.0",method="POST"} 1.0'
        in metrics_text
    )
    assert (
        'gateway_request_duration_seconds_bucket{department="dept-a",'
        'endpoint="chat_completions",le="120.0",method="POST"} 1.0'
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
        'gateway_http_request_failures_total{department="dept-a",'
        'endpoint="chat_completions",failure_origin="gateway",method="POST",'
        'status_class="5xx"} 1.0'
        in metrics_text
    )
    assert (
        'gateway_http_request_failures_total{department="dept-a",'
        'endpoint="chat_completions",failure_origin="upstream",method="POST",'
        'status_class="5xx"} 1.0'
        in metrics_text
    )


def test_upstream_observability_metrics_use_bounded_labels() -> None:
    metrics = GatewayMetrics()

    metrics.observe_upstream_request_duration(
        model_name="shared-model",
        upstream_name="gpu-a",
        endpoint="chat_completions",
        duration_seconds=0.25,
    )
    metrics.observe_stream_first_chunk(
        department="dept-a",
        model_name="shared-model",
        endpoint="chat_completions",
        duration_seconds=0.1,
    )
    metrics.record_upstream_selection(model_name="shared-model", upstream_name="gpu-a")

    metrics_text = generate_latest(metrics.registry).decode("utf-8")

    assert (
        'gateway_upstream_request_duration_seconds_bucket{endpoint="chat_completions",'
        'le="0.5",model_name="shared-model",upstream_name="gpu-a"} 1.0'
        in metrics_text
    )
    assert (
        'gateway_stream_first_chunk_seconds_bucket{department="dept-a",'
        'endpoint="chat_completions",le="0.5",model_name="shared-model"} 1.0'
        in metrics_text
    )
    assert (
        'gateway_upstream_selections_total{model_name="shared-model",upstream_name="gpu-a"} 1.0'
        in metrics_text
    )
