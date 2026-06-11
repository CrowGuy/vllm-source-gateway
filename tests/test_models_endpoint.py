from __future__ import annotations


def test_get_models_returns_public_model_inventory(app_client) -> None:
    response = app_client.get("/v1/models")

    assert response.status_code == 200
    assert response.json() == {
        "object": "list",
        "data": [
            {
                "id": "model-a",
                "object": "model",
                "status": "online",
                "healthy_upstreams": 1,
                "total_upstreams": 1,
            },
            {
                "id": "model-b",
                "object": "model",
                "status": "online",
                "healthy_upstreams": 1,
                "total_upstreams": 1,
            },
            {
                "id": "shared-model",
                "object": "model",
                "status": "online",
                "healthy_upstreams": 2,
                "total_upstreams": 2,
            },
        ],
    }


def test_get_models_reflects_current_upstream_health(app_client) -> None:
    app_client.app.state.routing_registry.set_upstream_health("gpu-a", healthy=False)

    response = app_client.get("/v1/models")
    assert response.status_code == 200

    data = {entry["id"]: entry for entry in response.json()["data"]}

    assert data["model-a"]["status"] == "unavailable"
    assert data["model-a"]["healthy_upstreams"] == 0
    assert data["model-a"]["total_upstreams"] == 1
    assert data["shared-model"]["status"] == "online"
    assert data["shared-model"]["healthy_upstreams"] == 1
    assert data["shared-model"]["total_upstreams"] == 2


def test_get_models_does_not_expose_upstream_addresses(app_client) -> None:
    response = app_client.get("/v1/models")
    payload = response.json()

    flattened_payload = str(payload)

    assert "10.0.0.1" not in flattened_payload
    assert "10.0.0.2" not in flattened_payload
