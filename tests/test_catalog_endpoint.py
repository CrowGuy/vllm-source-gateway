from __future__ import annotations

from fastapi.testclient import TestClient


def test_get_models_catalog_returns_html_with_all_models(app_client) -> None:
    response = app_client.get("/models")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "vLLM Model Catalog" in response.text
    assert "Internal service catalog" in response.text
    assert "summary-card" in response.text
    assert "model-card" in response.text
    assert "model-a" in response.text
    assert "model-b" in response.text
    assert "shared-model" in response.text
    assert "Not documented" in response.text


def test_get_models_catalog_includes_configured_metadata(
    sample_config_copy,
    write_config,
) -> None:
    sample_config_copy["model_catalog"] = {
        "model-a": {
            "display_name": "Model A",
            "hosted_on": "DGX-A100 production pool",
            "use_cases": ["coding", "general chat"],
            "api_paths": ["chat", "responses", "messages"],
            "context_window": "32k tokens",
            "recommended_for": ["code agents"],
            "known_limits": ["not intended for embeddings"],
            "example_prompt": "Reply with exactly: hello",
        }
    }
    config_path = write_config(sample_config_copy, filename="catalog.yaml")

    from vllm_source_gateway.main import create_app

    with TestClient(create_app(config_path=config_path)) as client:
        response = client.get("/models")

    assert response.status_code == 200
    assert "Model A" in response.text
    assert "DGX-A100 production pool" in response.text
    assert '<span class="chip">coding</span>' in response.text
    assert '<span class="chip">general chat</span>' in response.text
    assert '<span class="chip">chat</span>' in response.text
    assert '<span class="chip">responses</span>' in response.text
    assert '<span class="chip">messages</span>' in response.text
    assert "32k tokens" in response.text
    assert '<ul class="bullet-list"><li>code agents</li></ul>' in response.text
    assert '<ul class="bullet-list"><li>not intended for embeddings</li></ul>' in response.text
    assert "/v1/chat/completions" in response.text


def test_get_models_catalog_reflects_online_degraded_and_offline_status(app_client) -> None:
    app_client.app.state.routing_registry.set_upstream_health("gpu-a", healthy=False)

    response = app_client.get("/models")

    assert response.status_code == 200
    assert "status-offline" in response.text
    assert "0/1 upstreams" in response.text
    assert "status-degraded" in response.text
    assert "1/2 upstreams" in response.text


def test_get_models_catalog_includes_summary_counts(app_client) -> None:
    app_client.app.state.routing_registry.set_upstream_health("gpu-a", healthy=False)

    response = app_client.get("/models")

    assert response.status_code == 200
    assert "Total models" in response.text
    assert "Online" in response.text
    assert "Degraded" in response.text
    assert "Offline" in response.text
    assert "<strong>3</strong>" in response.text
    assert "<strong>1</strong>" in response.text


def test_get_models_catalog_does_not_expose_upstream_addresses_or_tokens(app_client) -> None:
    response = app_client.get("/models")

    assert response.status_code == 200
    assert "10.0.0.1" not in response.text
    assert "10.0.0.2" not in response.text
    assert "upstream-token-a" not in response.text
    assert "upstream-token-b" not in response.text


def test_get_models_catalog_escapes_configured_metadata(
    sample_config_copy,
    write_config,
) -> None:
    sample_config_copy["model_catalog"] = {
        "model-a": {
            "display_name": "<script>alert(1)</script>",
            "hosted_on": "Lab <GPU>",
            "use_cases": ["coding"],
        }
    }
    config_path = write_config(sample_config_copy, filename="catalog-escaping.yaml")

    from vllm_source_gateway.main import create_app

    with TestClient(create_app(config_path=config_path)) as client:
        response = client.get("/models")

    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "Lab &lt;GPU&gt;" in response.text
