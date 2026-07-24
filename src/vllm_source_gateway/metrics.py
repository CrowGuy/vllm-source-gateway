from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REQUEST_DURATION_BUCKETS = (
    0.1,
    0.5,
    1.0,
    2.0,
    5.0,
    10.0,
    20.0,
    30.0,
    60.0,
    120.0,
    300.0,
)


def _status_class(status_code: int) -> str:
    if 200 <= status_code < 300:
        return "2xx"
    if 400 <= status_code < 500:
        return "4xx"
    return "5xx"


class GatewayMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()

        self.http_requests_total = Counter(
            "gateway_http_requests_total",
            "Handled HTTP requests by department and endpoint.",
            labelnames=("department", "endpoint", "method", "status_class"),
            registry=self.registry,
        )
        self.request_duration_seconds = Histogram(
            "gateway_request_duration_seconds",
            "End-to-end gateway handling duration in seconds.",
            labelnames=("department", "endpoint", "method"),
            buckets=REQUEST_DURATION_BUCKETS,
            registry=self.registry,
        )
        self.http_request_failures_total = Counter(
            "gateway_http_request_failures_total",
            "Handled HTTP failures by department, endpoint, and failure origin.",
            labelnames=("department", "endpoint", "method", "status_class", "failure_origin"),
            registry=self.registry,
        )
        self.prompt_tokens_total = Counter(
            "gateway_prompt_tokens_total",
            "Prompt tokens recorded for completed requests with reliable usage.",
            labelnames=("department", "model_name"),
            registry=self.registry,
        )
        self.generation_tokens_total = Counter(
            "gateway_generation_tokens_total",
            "Generation tokens recorded for completed requests with reliable usage.",
            labelnames=("department", "model_name"),
            registry=self.registry,
        )
        self.source_resolution_total = Counter(
            "gateway_source_resolution_total",
            "Source resolution outcomes by department and resolution source.",
            labelnames=("department", "resolution_source"),
            registry=self.registry,
        )
        self.token_accounting_total = Counter(
            "gateway_token_accounting_total",
            "Token accounting outcomes by endpoint.",
            labelnames=("endpoint", "accounting_status"),
            registry=self.registry,
        )
        self.admission_rejections_total = Counter(
            "gateway_admission_rejections_total",
            "Admission control rejections by bounded reason.",
            labelnames=("department", "model_name", "reason"),
            registry=self.registry,
        )
        self.inflight_requests = Gauge(
            "gateway_inflight_requests",
            "Currently admitted in-flight requests.",
            labelnames=("department", "model_name", "endpoint"),
            registry=self.registry,
        )
        self.token_budget_rejections_total = Counter(
            "gateway_token_budget_rejections_total",
            "Token budget admission rejections.",
            labelnames=("department", "model_name"),
            registry=self.registry,
        )
        self.retry_guard_open_total = Counter(
            "gateway_retry_guard_open_total",
            "Retry guard cooldown openings.",
            labelnames=("department", "model_name"),
            registry=self.registry,
        )
        self.upstream_request_duration_seconds = Histogram(
            "gateway_upstream_request_duration_seconds",
            "Full non-streaming upstream request duration in seconds.",
            labelnames=("model_name", "upstream_name", "endpoint"),
            buckets=REQUEST_DURATION_BUCKETS,
            registry=self.registry,
        )
        self.stream_first_chunk_seconds = Histogram(
            "gateway_stream_first_chunk_seconds",
            "Streaming upstream first chunk latency in seconds.",
            labelnames=("department", "model_name", "endpoint"),
            buckets=REQUEST_DURATION_BUCKETS,
            registry=self.registry,
        )
        self.upstream_selections_total = Counter(
            "gateway_upstream_selections_total",
            "Successful upstream selections by model and upstream.",
            labelnames=("model_name", "upstream_name"),
            registry=self.registry,
        )

    def observe_request(
        self,
        *,
        department: str,
        endpoint: str,
        method: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        status_class = _status_class(status_code)
        self.http_requests_total.labels(
            department=department,
            endpoint=endpoint,
            method=method.upper(),
            status_class=status_class,
        ).inc()
        self.request_duration_seconds.labels(
            department=department,
            endpoint=endpoint,
            method=method.upper(),
        ).observe(duration_seconds)

    def record_request_failure(
        self,
        *,
        department: str,
        endpoint: str,
        method: str,
        status_code: int,
        failure_origin: str,
    ) -> None:
        if status_code < 400:
            return

        status_class = _status_class(status_code)
        self.http_request_failures_total.labels(
            department=department,
            endpoint=endpoint,
            method=method.upper(),
            status_class=status_class,
            failure_origin=failure_origin,
        ).inc()

    def record_prompt_tokens(self, *, department: str, model_name: str, prompt_tokens: int) -> None:
        if prompt_tokens <= 0:
            return
        self.prompt_tokens_total.labels(
            department=department,
            model_name=model_name,
        ).inc(prompt_tokens)

    def record_generation_tokens(
        self, *, department: str, model_name: str, generation_tokens: int
    ) -> None:
        if generation_tokens <= 0:
            return
        self.generation_tokens_total.labels(
            department=department,
            model_name=model_name,
        ).inc(generation_tokens)

    def record_source_resolution(self, *, department: str, resolution_source: str) -> None:
        self.source_resolution_total.labels(
            department=department,
            resolution_source=resolution_source,
        ).inc()

    def record_token_accounting(self, *, endpoint: str, accounting_status: str) -> None:
        self.token_accounting_total.labels(
            endpoint=endpoint,
            accounting_status=accounting_status,
        ).inc()

    def record_admission_rejection(
        self, *, department: str, model_name: str, reason: str
    ) -> None:
        self.admission_rejections_total.labels(
            department=department,
            model_name=model_name,
            reason=reason,
        ).inc()

    def inc_inflight_request(self, *, department: str, model_name: str, endpoint: str) -> None:
        self.inflight_requests.labels(
            department=department,
            model_name=model_name,
            endpoint=endpoint,
        ).inc()

    def dec_inflight_request(self, *, department: str, model_name: str, endpoint: str) -> None:
        self.inflight_requests.labels(
            department=department,
            model_name=model_name,
            endpoint=endpoint,
        ).dec()

    def record_token_budget_rejection(self, *, department: str, model_name: str) -> None:
        self.token_budget_rejections_total.labels(
            department=department,
            model_name=model_name,
        ).inc()

    def record_retry_guard_open(self, *, department: str, model_name: str) -> None:
        self.retry_guard_open_total.labels(
            department=department,
            model_name=model_name,
        ).inc()

    def observe_upstream_request_duration(
        self,
        *,
        model_name: str,
        upstream_name: str,
        endpoint: str,
        duration_seconds: float,
    ) -> None:
        self.upstream_request_duration_seconds.labels(
            model_name=model_name,
            upstream_name=upstream_name,
            endpoint=endpoint,
        ).observe(duration_seconds)

    def observe_stream_first_chunk(
        self,
        *,
        department: str,
        model_name: str,
        endpoint: str,
        duration_seconds: float,
    ) -> None:
        self.stream_first_chunk_seconds.labels(
            department=department,
            model_name=model_name,
            endpoint=endpoint,
        ).observe(duration_seconds)

    def record_upstream_selection(self, *, model_name: str, upstream_name: str) -> None:
        self.upstream_selections_total.labels(
            model_name=model_name,
            upstream_name=upstream_name,
        ).inc()
