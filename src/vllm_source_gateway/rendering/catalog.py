from __future__ import annotations

import json
from html import escape

from vllm_source_gateway.config import ModelCatalogEntry
from vllm_source_gateway.models import ModelAvailability


def _catalog_status(model: ModelAvailability) -> str:
    if model.healthy_upstream_count == 0:
        return "offline"
    if model.healthy_upstream_count < model.total_upstream_count:
        return "degraded"
    return "online"


def _text(value: str | int | None) -> str:
    if value is None:
        return "Not documented"
    if isinstance(value, int):
        return escape(f"{value:,}")
    normalized = value.strip()
    if not normalized:
        return "Not documented"
    return escape(normalized)


def _list(values: list[str]) -> str:
    if not values:
        return "Not documented"
    return ", ".join(escape(value) for value in values)


def _bullet_list(values: list[str]) -> str:
    if not values:
        return '<span class="muted">Not documented</span>'
    items = "".join(f"<li>{escape(value)}</li>" for value in values)
    return f'<ul class="bullet-list">{items}</ul>'


def _chips(values: list[str]) -> str:
    if not values:
        return '<span class="muted">Not documented</span>'
    return "".join(f'<span class="chip">{escape(value)}</span>' for value in values)


def _display_name(value: str | None) -> str:
    if not value:
        return ""
    return f"<p>{escape(value)}</p>"


def _summary_cards(models: list[ModelAvailability]) -> str:
    counts = {"online": 0, "degraded": 0, "offline": 0}
    for model in models:
        counts[_catalog_status(model)] += 1

    total = len(models)
    return f"""
      <section class="summary-grid" aria-label="model availability summary">
        <article class="summary-card">
          <span class="summary-label">Total models</span>
          <strong>{total}</strong>
        </article>
        <article class="summary-card">
          <span class="summary-label">Online</span>
          <strong>{counts["online"]}</strong>
        </article>
        <article class="summary-card">
          <span class="summary-label">Degraded</span>
          <strong>{counts["degraded"]}</strong>
        </article>
        <article class="summary-card">
          <span class="summary-label">Offline</span>
          <strong>{counts["offline"]}</strong>
        </article>
      </section>
    """


def _example_curl(model_name: str, catalog_entry: ModelCatalogEntry | None) -> str:
    prompt = (
        catalog_entry.example_prompt
        if catalog_entry and catalog_entry.example_prompt
        else "Say hello."
    )
    api_paths = catalog_entry.api_paths if catalog_entry else []
    selected_path = api_paths[0] if api_paths else "chat"

    if selected_path == "responses":
        body = json.dumps({"model": model_name, "input": prompt})
        command = (
            "curl -sS -H 'content-type: application/json' -H 'x-api-key: ${API_KEY}' "
            "-X POST '${GATEWAY_URL}/v1/responses' "
            f"--data '{body}'"
        )
    elif selected_path == "messages":
        body = json.dumps(
            {
                "model": model_name,
                "max_tokens": 64,
                "messages": [{"role": "user", "content": prompt}],
            }
        )
        command = (
            "curl -sS -H 'content-type: application/json' -H 'x-api-key: ${API_KEY}' "
            "-H 'anthropic-version: 2023-06-01' "
            "-X POST '${GATEWAY_URL}/v1/messages' "
            f"--data '{body}'"
        )
    else:
        body = json.dumps(
            {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
            }
        )
        command = (
            "curl -sS -H 'content-type: application/json' -H 'x-api-key: ${API_KEY}' "
            "-X POST '${GATEWAY_URL}/v1/chat/completions' "
            f"--data '{body}'"
        )

    return escape(command)


def _render_card(model: ModelAvailability, catalog_entry: ModelCatalogEntry | None) -> str:
    model_name = model.model_name
    status = _catalog_status(model)
    display_name = catalog_entry.display_name if catalog_entry else None

    return f"""
      <article class="model-card">
        <header class="model-card-header">
          <div>
            <h2>{escape(model_name)}</h2>
            {_display_name(display_name)}
          </div>
          <div class="status-wrap">
            <span class="status status-{escape(status)}">{escape(status)}</span>
            <span class="upstream-count">
              {model.healthy_upstream_count}/{model.total_upstream_count} upstreams
            </span>
          </div>
        </header>

        <dl class="model-details">
          <div>
            <dt>Hosted on</dt>
            <dd>{_text(catalog_entry.hosted_on if catalog_entry else None)}</dd>
          </div>
          <div>
            <dt>Context window</dt>
            <dd>{_text(catalog_entry.context_window if catalog_entry else None)}</dd>
          </div>
        </dl>

        <section>
          <h3>API paths</h3>
          <div class="chip-row">{_chips(catalog_entry.api_paths if catalog_entry else [])}</div>
        </section>
        <section>
          <h3>Use cases</h3>
          <div class="chip-row">{_chips(catalog_entry.use_cases if catalog_entry else [])}</div>
        </section>
        <section>
          <h3>Recommended for</h3>
          {_bullet_list(catalog_entry.recommended_for if catalog_entry else [])}
        </section>
        <section>
          <h3>Known limits</h3>
          {_bullet_list(catalog_entry.known_limits if catalog_entry else [])}
        </section>
        <section>
          <h3>Example curl</h3>
          <pre><code>{_example_curl(model_name, catalog_entry)}</code></pre>
        </section>
      </article>
    """


def render_catalog_page(
    *,
    models: list[ModelAvailability],
    model_catalog: dict[str, ModelCatalogEntry],
) -> str:
    cards = "\n".join(
        _render_card(
            model=model,
            catalog_entry=model_catalog.get(model.model_name),
        )
        for model in models
    )
    summary_cards = _summary_cards(models)

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>vLLM Model Catalog</title>
    <style>
      :root {{
        color-scheme: light;
        --bg: #f6f4ee;
        --panel: #fffaf1;
        --panel-strong: #ffffff;
        --ink: #18211f;
        --muted: #65716d;
        --line: #ded7c9;
        --accent: #245c4f;
        --chip: #e7efe9;
        --online: #177245;
        --degraded: #b36b00;
        --offline: #a33a2b;
        --shadow: 0 18px 45px rgba(44, 35, 20, 0.09);
      }}

      * {{ box-sizing: border-box; }}

      body {{
        margin: 0;
        background:
          radial-gradient(circle at top left, rgba(36, 92, 79, 0.14), transparent 32rem),
          linear-gradient(135deg, #f8f5ef 0%, #efe8da 100%);
        color: var(--ink);
        font-family:
          ui-sans-serif,
          system-ui,
          -apple-system,
          BlinkMacSystemFont,
          "Segoe UI",
          sans-serif;
      }}

      main {{ max-width: 1180px; margin: 0 auto; padding: 3rem 1.25rem; }}

      .hero {{
        display: grid;
        gap: 0.75rem;
        margin-bottom: 1.5rem;
      }}

      .eyebrow {{
        color: var(--accent);
        font-size: 0.78rem;
        font-weight: 800;
        letter-spacing: 0.12em;
        text-transform: uppercase;
      }}

      h1 {{ font-size: clamp(2.1rem, 4vw, 4rem); letter-spacing: -0.05em; margin: 0; }}
      h2 {{ font-size: 1.25rem; letter-spacing: -0.03em; margin: 0; }}
      h3 {{
        color: var(--ink);
        font-size: 0.78rem;
        font-weight: 900;
        letter-spacing: 0.08em;
        margin: 0 0 0.45rem;
        text-transform: uppercase;
      }}
      p {{ color: var(--muted); line-height: 1.6; margin: 0; }}

      .summary-grid {{
        display: grid;
        gap: 0.85rem;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        margin: 2rem 0;
      }}

      .summary-card {{
        background: rgba(255, 250, 241, 0.82);
        border: 1px solid var(--line);
        border-radius: 1.1rem;
        padding: 1rem;
        box-shadow: var(--shadow);
      }}

      .summary-label {{ color: var(--muted); display: block; font-size: 0.82rem; }}
      .summary-card strong {{ display: block; font-size: 2rem; margin-top: 0.25rem; }}

      .model-grid {{
        display: grid;
        gap: 1rem;
        grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      }}

      .model-card {{
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 1.35rem;
        box-shadow: var(--shadow);
        display: grid;
        gap: 1rem;
        padding: 1.15rem;
      }}

      .model-card-header {{
        align-items: flex-start;
        display: flex;
        gap: 1rem;
        justify-content: space-between;
      }}

      .status-wrap {{ display: grid; gap: 0.35rem; justify-items: end; white-space: nowrap; }}
      .status {{
        border-radius: 999px;
        color: white;
        font-size: 0.78rem;
        font-weight: 800;
        padding: 0.25rem 0.65rem;
      }}
      .status-online {{ background: var(--online); }}
      .status-degraded {{ background: var(--degraded); }}
      .status-offline {{ background: var(--offline); }}
      .upstream-count, .muted {{ color: var(--muted); font-size: 0.85rem; }}

      .model-details {{
        display: grid;
        gap: 0.75rem;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        margin: 0;
      }}

      .model-details div {{
        background: var(--panel-strong);
        border: 1px solid var(--line);
        border-radius: 0.9rem;
        padding: 0.8rem;
      }}
      dt {{ color: var(--muted); font-size: 0.78rem; font-weight: 700; text-transform: uppercase; }}
      dd {{ margin: 0.2rem 0 0; }}

      .chip-row {{ display: flex; flex-wrap: wrap; gap: 0.45rem; }}
      .chip {{
        background: var(--chip);
        border: 1px solid #ccdcd1;
        border-radius: 999px;
        color: var(--accent);
        font-size: 0.82rem;
        font-weight: 700;
        padding: 0.25rem 0.6rem;
      }}

      .bullet-list {{
        color: var(--muted);
        line-height: 1.55;
        margin: 0;
        padding-left: 1.15rem;
      }}

      .bullet-list li + li {{ margin-top: 0.25rem; }}

      pre {{
        background: #17211f;
        border-radius: 0.95rem;
        color: #eef7ef;
        margin: 0;
        overflow-x: auto;
        padding: 0.85rem;
      }}

      code {{ font-size: 0.82rem; white-space: pre-wrap; word-break: break-word; }}

      @media (max-width: 760px) {{
        main {{ padding: 2rem 1rem; }}
        .summary-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
        .model-card-header {{ display: grid; }}
        .status-wrap {{ justify-items: start; }}
        .model-details {{ grid-template-columns: 1fr; }}
      }}
    </style>
  </head>
  <body>
    <main>
      <section class="hero">
        <span class="eyebrow">Internal service catalog</span>
        <h1>vLLM Model Catalog</h1>
        <p>
          A read-only guide for choosing the right model and API path. Use
          <code>/v1/models</code> for machine-readable model discovery.
        </p>
      </section>
      {summary_cards}
      <section class="model-grid" aria-label="models">
        {cards}
      </section>
    </main>
  </body>
</html>
"""
