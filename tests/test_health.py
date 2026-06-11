from __future__ import annotations


def test_healthz_returns_service_counts(app_client) -> None:
    response = app_client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "upstream_count": 2,
        "model_count": 3,
        "department_count": 2,
    }


def test_metrics_endpoint_is_scrapeable(app_client) -> None:
    response = app_client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "gateway_http_requests_total" in response.text
    assert "gateway_request_duration_seconds" in response.text
