from __future__ import annotations

from vllm_source_gateway.config import ModelCatalogEntry
from vllm_source_gateway.models import ModelAvailability
from vllm_source_gateway.rendering.catalog import render_catalog_page


def test_render_catalog_page_includes_summary_cards_and_model_card() -> None:
    html = render_catalog_page(
        models=[
            ModelAvailability(
                model_name="model-a",
                healthy_upstream_count=1,
                total_upstream_count=1,
            )
        ],
        model_catalog={},
    )

    assert "summary-card" in html
    assert "model-card" in html
    assert "Total models" in html
    assert "model-a" in html
    assert "status-online" in html


def test_render_catalog_page_omits_missing_display_name() -> None:
    html = render_catalog_page(
        models=[
            ModelAvailability(
                model_name="model-a",
                healthy_upstream_count=1,
                total_upstream_count=1,
            )
        ],
        model_catalog={},
    )

    model_heading_index = html.index("<h2>model-a</h2>")
    next_section_index = html.index('<div class="status-wrap">', model_heading_index)
    model_heading_block = html[model_heading_index:next_section_index]

    assert "Not documented" not in model_heading_block


def test_render_catalog_page_escapes_metadata() -> None:
    html = render_catalog_page(
        models=[
            ModelAvailability(
                model_name="model-a",
                healthy_upstream_count=1,
                total_upstream_count=1,
            )
        ],
        model_catalog={
            "model-a": ModelCatalogEntry(
                display_name="<script>alert(1)</script>",
                hosted_on="Lab <GPU>",
                use_cases=["coding"],
            )
        },
    )

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "Lab &lt;GPU&gt;" in html


def test_render_catalog_page_uses_bold_black_section_labels() -> None:
    html = render_catalog_page(
        models=[
            ModelAvailability(
                model_name="model-a",
                healthy_upstream_count=1,
                total_upstream_count=1,
            )
        ],
        model_catalog={},
    )

    assert "color: var(--ink);" in html
    assert "font-weight: 900;" in html
    assert "<h3>API paths</h3>" in html
    assert "<h3>Use cases</h3>" in html
    assert "<h3>Recommended for</h3>" in html
    assert "<h3>Known limits</h3>" in html
    assert "<h3>Example curl</h3>" in html
