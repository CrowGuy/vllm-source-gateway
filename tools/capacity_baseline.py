#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx


DEFAULT_PATHS = ("chat", "responses", "messages")
PATH_TO_ENDPOINT = {
    "chat": "/v1/chat/completions",
    "responses": "/v1/responses",
    "messages": "/v1/messages",
}


@dataclass(frozen=True)
class RequestResult:
    model: str
    path: str
    concurrency: int
    request_index: int
    ok: bool
    status_code: int | None
    first_chunk_seconds: float | None
    total_seconds: float
    bytes_received: int
    chunks_received: int
    error: str | None


@dataclass(frozen=True)
class LevelSummary:
    model: str
    path: str
    concurrency: int
    total_requests: int
    success_count: int
    failure_count: int
    p50_total_seconds: float | None
    p95_total_seconds: float | None
    max_total_seconds: float | None
    p50_first_chunk_seconds: float | None
    p95_first_chunk_seconds: float | None
    max_first_chunk_seconds: float | None


def parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_int_csv(value: str) -> list[int]:
    parsed = [int(item) for item in parse_csv(value)]
    if not parsed:
        raise argparse.ArgumentTypeError("must include at least one integer")
    if any(item <= 0 for item in parsed):
        raise argparse.ArgumentTypeError("all concurrency levels must be positive")
    return parsed


def parse_mixed_models(value: str) -> dict[str, int]:
    entries: dict[str, int] = {}
    for item in parse_csv(value):
        if ":" not in item:
            raise argparse.ArgumentTypeError(
                "mixed models must use model:concurrency entries",
            )
        model, concurrency_text = item.rsplit(":", 1)
        model = model.strip()
        if not model:
            raise argparse.ArgumentTypeError("mixed model name cannot be empty")
        try:
            concurrency = int(concurrency_text)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"invalid concurrency for mixed model {model}: {concurrency_text}",
            ) from exc
        if concurrency <= 0:
            raise argparse.ArgumentTypeError(
                f"mixed model concurrency must be positive for {model}",
            )
        entries[model] = concurrency

    if not entries:
        raise argparse.ArgumentTypeError("must include at least one model:concurrency entry")
    return entries


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percent)))
    return ordered[index]


def build_payload(path: str, model: str, prompt: str, max_tokens: int) -> dict[str, Any]:
    if path == "chat":
        return {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": True,
        }
    if path == "responses":
        return {
            "model": model,
            "input": prompt,
            "max_output_tokens": max_tokens,
            "stream": True,
        }
    if path == "messages":
        return {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": True,
        }
    raise ValueError(f"unsupported path: {path}")


def build_headers(path: str, api_key: str) -> dict[str, str]:
    headers = {
        "content-type": "application/json",
        "x-api-key": api_key,
    }
    if path == "messages":
        headers["anthropic-version"] = "2023-06-01"
    return headers


async def scrape_metrics(
    client: httpx.AsyncClient,
    gateway_base_url: str,
    output_file: Path,
) -> None:
    response = await client.get(f"{gateway_base_url}/metrics")
    output_file.write_text(response.text, encoding="utf-8")


async def run_streaming_request(
    *,
    client: httpx.AsyncClient,
    gateway_base_url: str,
    api_key: str,
    model: str,
    path: str,
    concurrency: int,
    request_index: int,
    prompt: str,
    max_tokens: int,
) -> RequestResult:
    endpoint = PATH_TO_ENDPOINT[path]
    payload = build_payload(path=path, model=model, prompt=prompt, max_tokens=max_tokens)
    headers = build_headers(path=path, api_key=api_key)

    started_at = time.perf_counter()
    first_chunk_at: float | None = None
    bytes_received = 0
    chunks_received = 0
    status_code: int | None = None

    try:
        async with client.stream(
            "POST",
            f"{gateway_base_url}{endpoint}",
            headers=headers,
            json=payload,
        ) as response:
            status_code = response.status_code
            async for chunk in response.aiter_bytes():
                if not chunk:
                    continue
                chunks_received += 1
                bytes_received += len(chunk)
                if first_chunk_at is None:
                    first_chunk_at = time.perf_counter()

        total_seconds = time.perf_counter() - started_at
        first_chunk_seconds = None if first_chunk_at is None else first_chunk_at - started_at
        ok = status_code is not None and 200 <= status_code < 300 and chunks_received > 0
        return RequestResult(
            model=model,
            path=path,
            concurrency=concurrency,
            request_index=request_index,
            ok=ok,
            status_code=status_code,
            first_chunk_seconds=first_chunk_seconds,
            total_seconds=total_seconds,
            bytes_received=bytes_received,
            chunks_received=chunks_received,
            error=None if ok else "non_2xx_or_empty_stream",
        )
    except Exception as exc:  # noqa: BLE001 - capacity runs should report per-request failures.
        return RequestResult(
            model=model,
            path=path,
            concurrency=concurrency,
            request_index=request_index,
            ok=False,
            status_code=status_code,
            first_chunk_seconds=None,
            total_seconds=time.perf_counter() - started_at,
            bytes_received=bytes_received,
            chunks_received=chunks_received,
            error=f"{type(exc).__name__}: {exc}",
        )


async def run_level(
    *,
    client: httpx.AsyncClient,
    gateway_base_url: str,
    api_key: str,
    model: str,
    path: str,
    concurrency: int,
    requests_per_level: int,
    prompt: str,
    max_tokens: int,
) -> list[RequestResult]:
    results: list[RequestResult] = []
    next_index = 0

    while next_index < requests_per_level:
        batch_size = min(concurrency, requests_per_level - next_index)
        tasks = [
            run_streaming_request(
                client=client,
                gateway_base_url=gateway_base_url,
                api_key=api_key,
                model=model,
                path=path,
                concurrency=concurrency,
                request_index=next_index + offset,
                prompt=prompt,
                max_tokens=max_tokens,
            )
            for offset in range(batch_size)
        ]
        results.extend(await asyncio.gather(*tasks))
        next_index += batch_size

    return results


async def run_mixed_level(
    *,
    client: httpx.AsyncClient,
    gateway_base_url: str,
    api_key: str,
    mixed_models: dict[str, int],
    path: str,
    requests_per_level: int,
    prompt: str,
    max_tokens: int,
) -> dict[str, list[RequestResult]]:
    tasks = [
        run_level(
            client=client,
            gateway_base_url=gateway_base_url,
            api_key=api_key,
            model=model,
            path=path,
            concurrency=concurrency,
            requests_per_level=requests_per_level,
            prompt=prompt,
            max_tokens=max_tokens,
        )
        for model, concurrency in mixed_models.items()
    ]
    results_by_model = await asyncio.gather(*tasks)
    return {
        model: results
        for model, results in zip(mixed_models, results_by_model, strict=True)
    }


def summarize(
    *,
    model: str,
    path: str,
    concurrency: int,
    results: list[RequestResult],
) -> LevelSummary:
    successes = [result for result in results if result.ok]
    total_durations = [result.total_seconds for result in successes]
    first_chunk_durations = [
        result.first_chunk_seconds
        for result in successes
        if result.first_chunk_seconds is not None
    ]

    return LevelSummary(
        model=model,
        path=path,
        concurrency=concurrency,
        total_requests=len(results),
        success_count=len(successes),
        failure_count=len(results) - len(successes),
        p50_total_seconds=statistics.median(total_durations) if total_durations else None,
        p95_total_seconds=percentile(total_durations, 0.95),
        max_total_seconds=max(total_durations) if total_durations else None,
        p50_first_chunk_seconds=(
            statistics.median(first_chunk_durations) if first_chunk_durations else None
        ),
        p95_first_chunk_seconds=percentile(first_chunk_durations, 0.95),
        max_first_chunk_seconds=max(first_chunk_durations) if first_chunk_durations else None,
    )


def write_markdown_report(
    *,
    output_file: Path,
    gateway_base_url: str,
    models: list[str],
    mode: str,
    prompt_source: str,
    prompt_chars: int,
    paths: list[str],
    concurrency_levels: list[int],
    mixed_models: dict[str, int] | None,
    requests_per_level: int,
    summaries: list[LevelSummary],
) -> None:
    concurrency_line = (
        f"- mixed models: `{','.join(f'{model}:{level}' for model, level in mixed_models.items())}`"
        if mixed_models
        else f"- concurrency levels: `{','.join(str(level) for level in concurrency_levels)}`"
    )
    lines = [
        "# vLLM Source Gateway Capacity Baseline",
        "",
        "## Inputs",
        "",
        f"- gateway: `{gateway_base_url}`",
        f"- mode: `{mode}`",
        f"- models: `{','.join(models)}`",
        f"- prompt source: `{prompt_source}`",
        f"- prompt characters: `{prompt_chars}`",
        f"- paths: `{','.join(paths)}`",
        concurrency_line,
        f"- requests per level: `{requests_per_level}`",
        "",
        "## Summary",
        "",
        "| Model | Path | Concurrency | Requests | Success | Failure | "
        "P50 total | P95 total | Max total | P50 first chunk | P95 first chunk |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    def fmt(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.3f}s"

    for summary in summaries:
        lines.append(
            "| "
            + " | ".join(
                [
                    summary.model,
                    summary.path,
                    str(summary.concurrency),
                    str(summary.total_requests),
                    str(summary.success_count),
                    str(summary.failure_count),
                    fmt(summary.p50_total_seconds),
                    fmt(summary.p95_total_seconds),
                    fmt(summary.max_total_seconds),
                    fmt(summary.p50_first_chunk_seconds),
                    fmt(summary.p95_first_chunk_seconds),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Acceptance Notes",
            "",
            "- Treat this as a lightweight baseline, not a maximum throughput benchmark.",
            "- Confirm `/livez`, `/readyz`, container CPU/memory, and gateway metrics "
            "after the run.",
            "- Investigate any gateway-origin failures before raising concurrency expectations.",
        ]
    )
    output_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run(args: argparse.Namespace) -> int:
    import httpx

    prompt = resolve_prompt(args)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timeout = httpx.Timeout(args.timeout_seconds)
    max_concurrency = (
        sum(args.mixed_models.values())
        if args.mixed_models
        else max(args.concurrency_levels)
    )
    limits = httpx.Limits(max_connections=max_concurrency + 10)
    all_results: list[RequestResult] = []
    summaries: list[LevelSummary] = []

    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        await scrape_metrics(client, args.gateway_base_url, output_dir / "metrics_before.prom")

        for path in args.paths:
            if args.mixed_models:
                mixed_description = ",".join(
                    f"{model}:{concurrency}"
                    for model, concurrency in args.mixed_models.items()
                )
                print(
                    f"running path={path} mixed_models={mixed_description} "
                    f"requests={args.requests_per_level}"
                )
                mixed_results = await run_mixed_level(
                    client=client,
                    gateway_base_url=args.gateway_base_url,
                    api_key=args.api_key,
                    mixed_models=args.mixed_models,
                    path=path,
                    requests_per_level=args.requests_per_level,
                    prompt=prompt,
                    max_tokens=args.max_tokens,
                )
                for model, level_results in mixed_results.items():
                    all_results.extend(level_results)
                    summary = summarize(
                        model=model,
                        path=path,
                        concurrency=args.mixed_models[model],
                        results=level_results,
                    )
                    summaries.append(summary)
                    print(
                        f"  model={model} success={summary.success_count}/"
                        f"{summary.total_requests} p95_total={summary.p95_total_seconds}"
                    )
            else:
                for concurrency in args.concurrency_levels:
                    print(
                        f"running path={path} concurrency={concurrency} "
                        f"requests={args.requests_per_level}"
                    )
                    level_results = await run_level(
                        client=client,
                        gateway_base_url=args.gateway_base_url,
                        api_key=args.api_key,
                        model=args.model,
                        path=path,
                        concurrency=concurrency,
                        requests_per_level=args.requests_per_level,
                        prompt=prompt,
                        max_tokens=args.max_tokens,
                    )
                    all_results.extend(level_results)
                    summary = summarize(
                        model=args.model,
                        path=path,
                        concurrency=concurrency,
                        results=level_results,
                    )
                    summaries.append(summary)
                    print(
                        f"  success={summary.success_count}/{summary.total_requests} "
                        f"p95_total={summary.p95_total_seconds}"
                    )

        await scrape_metrics(client, args.gateway_base_url, output_dir / "metrics_after.prom")

    (output_dir / "results.json").write_text(
        json.dumps([asdict(result) for result in all_results], indent=2),
        encoding="utf-8",
    )
    (output_dir / "summary.json").write_text(
        json.dumps([asdict(summary) for summary in summaries], indent=2),
        encoding="utf-8",
    )
    write_markdown_report(
        output_file=output_dir / "summary.md",
        gateway_base_url=args.gateway_base_url,
        models=list(args.mixed_models) if args.mixed_models else [args.model],
        mode="mixed-model" if args.mixed_models else "single-model",
        prompt_source=args.prompt_file or "inline",
        prompt_chars=len(prompt),
        paths=args.paths,
        concurrency_levels=args.concurrency_levels,
        mixed_models=args.mixed_models,
        requests_per_level=args.requests_per_level,
        summaries=summaries,
    )

    failures = sum(summary.failure_count for summary in summaries)
    print()
    print("Capacity baseline completed.")
    print(f"Artifacts: {output_dir}")
    print(f"Failures: {failures}")
    return 1 if failures and args.fail_on_error else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a lightweight single-process streaming capacity baseline.",
    )
    parser.add_argument(
        "--gateway-base-url",
        default=os.environ.get("GATEWAY_BASE_URL", "http://127.0.0.1:8080"),
    )
    parser.add_argument("--api-key", default=os.environ.get("API_KEY"))
    parser.add_argument("--model", default=os.environ.get("MODEL_NAME"))
    parser.add_argument(
        "--mixed-models",
        type=parse_mixed_models,
        default=None,
        help=(
            "Run a mixed-model baseline with model:concurrency entries, "
            "for example model-a:10,model-b:5. Overrides --model and "
            "--concurrency-levels."
        ),
    )
    parser.add_argument(
        "--paths",
        type=parse_csv,
        default=list(DEFAULT_PATHS),
        help="Comma-separated list: chat,responses,messages",
    )
    parser.add_argument(
        "--concurrency-levels",
        type=parse_int_csv,
        default=[1, 5, 10],
        help="Comma-separated positive integers, for example 1,5,10,20",
    )
    parser.add_argument("--requests-per-level", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=float, default=180)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument(
        "--prompt",
        default="Count to 5 in one short line. Keep the answer concise.",
    )
    parser.add_argument(
        "--prompt-file",
        default=os.environ.get("PROMPT_FILE"),
        help="Read the request prompt from a UTF-8 text file. Overrides --prompt.",
    )
    parser.add_argument(
        "--output-dir",
        default="/tmp/vllm-source-gateway-capacity",
    )
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Return a non-zero exit code when any request fails.",
    )
    return parser


def resolve_prompt(args: argparse.Namespace) -> str:
    if not args.prompt_file:
        return args.prompt

    prompt_file = Path(args.prompt_file)
    try:
        prompt = prompt_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"failed to read --prompt-file {prompt_file}: {exc}") from exc

    if not prompt.strip():
        raise SystemExit(f"--prompt-file {prompt_file} is empty")
    return prompt


def validate_args(args: argparse.Namespace) -> None:
    if not args.api_key:
        raise SystemExit("missing --api-key or API_KEY")
    if not args.model and not args.mixed_models:
        raise SystemExit("missing --model or MODEL_NAME")
    if args.requests_per_level <= 0:
        raise SystemExit("--requests-per-level must be positive")
    unsupported_paths = sorted(set(args.paths) - set(PATH_TO_ENDPOINT))
    if unsupported_paths:
        raise SystemExit(f"unsupported --paths value(s): {','.join(unsupported_paths)}")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    validate_args(args)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
