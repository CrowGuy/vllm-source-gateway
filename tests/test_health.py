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


def test_livez_returns_service_counts(app_client) -> None:
    response = app_client.get("/livez")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "upstream_count": 2,
        "model_count": 3,
        "department_count": 2,
    }


def test_readyz_returns_ok_when_at_least_one_upstream_is_healthy(app_client) -> None:
    response = app_client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "healthy_upstream_count": 2,
        "total_upstream_count": 2,
    }


def test_readyz_returns_503_when_no_upstreams_are_healthy(app_client) -> None:
    app_client.app.state.routing_registry.set_upstream_health("gpu-a", healthy=False)
    app_client.app.state.routing_registry.set_upstream_health("gpu-b", healthy=False)

    response = app_client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "healthy_upstream_count": 0,
        "total_upstream_count": 2,
    }


def test_metrics_endpoint_is_scrapeable(app_client) -> None:
    response = app_client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "gateway_http_requests_total" in response.text
    assert "gateway_request_duration_seconds" in response.text
