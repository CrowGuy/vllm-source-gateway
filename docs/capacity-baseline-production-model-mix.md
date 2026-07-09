# Production Model Mix Capacity Baseline

## Summary

This document records the initial production capacity baseline for the current production model
mix served through `vllm-source-gateway`.

Current status: the gateway is launch-ready for the current single-process,
current-model-mix production baseline. Same-model multi-upstream round-robin and
connect-stage failover behavior have both been validated in production.

It includes:

- single-model baseline profiles for `gpt-oss-120b-local`
- mixed-model baseline profiles for `gpt-oss-120b-local` and `qwen3.6-35b-a3b`

This is a single-process, streaming production-confidence baseline, not a maximum throughput
benchmark. It verifies that the gateway can proxy streaming traffic across the frozen API surface
without unexpected gateway-origin failures under representative output and context profiles.

Validated API paths:

- `POST /v1/chat/completions`
- `POST /v1/responses`
- `POST /v1/messages`

All completed single-model and mixed-model profiles had `100%` request success.

## Test Context

Common inputs:

- single-model profile model: `gpt-oss-120b-local`
- mixed-model profile models: `gpt-oss-120b-local`, `qwen3.6-35b-a3b`
- paths: `chat,responses,messages`
- gateway deployment: single process / single worker baseline
- test tool: `tools/capacity_baseline.py`

Run metadata:

- environment: production
- gateway mode: single process / single worker
- profile type: production-confidence baseline
- request mode: streaming only (`stream: true`)
- complete API surface under test: `chat`, `responses`, `messages`
- result scope: gateway plus real upstream behavior for the current production model mix

Known run identity:

- gateway image / git revision: not captured in the original run artifacts
- vLLM version: not captured in the original run artifacts
- production host shape: not captured in the original run artifacts
- gateway config revision or checksum: not captured in the original run artifacts
- run date: not captured in the original run artifacts

Future baseline refreshes should record the missing identity fields above before replacing these
tables. Without those fields, the results are still useful as production evidence, but they are
harder to compare across model, gateway, vLLM, host, or config changes.

Interpretation boundaries:

- These results characterize the current production deployment shape.
- These results characterize streaming request behavior only; they do not claim a separate
  non-streaming capacity baseline.
- These results should not be treated as maximum throughput or autoscaling thresholds.
- Re-run this baseline after changing the model, vLLM version, gateway config, host shape, or
  upstream deployment topology.

## Normal Output Baseline

Inputs:

- prompt source: inline
- paths: `chat,responses,messages`
- concurrency levels: `1,5,10,20`
- requests per level: `30`

| Path | Concurrency | Requests | Success | Failure | P50 total | P95 total | Max total | P50 first chunk | P95 first chunk |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| chat | 1 | 30 | 30 | 0 | 0.755s | 1.155s | 1.243s | 0.022s | 0.030s |
| chat | 5 | 30 | 30 | 0 | 1.149s | 1.630s | 1.696s | 0.043s | 0.046s |
| chat | 10 | 30 | 30 | 0 | 1.219s | 1.544s | 1.865s | 0.044s | 0.053s |
| chat | 20 | 30 | 30 | 0 | 1.475s | 2.143s | 2.393s | 0.055s | 0.085s |
| responses | 1 | 30 | 30 | 0 | 0.703s | 1.266s | 1.274s | 0.005s | 0.007s |
| responses | 5 | 30 | 30 | 0 | 1.038s | 1.650s | 1.853s | 0.015s | 0.016s |
| responses | 10 | 30 | 30 | 0 | 1.351s | 2.075s | 2.107s | 0.024s | 0.026s |
| responses | 20 | 30 | 30 | 0 | 1.457s | 2.154s | 2.190s | 0.024s | 0.049s |
| messages | 1 | 30 | 30 | 0 | 0.729s | 1.225s | 1.435s | 0.020s | 0.022s |
| messages | 5 | 30 | 30 | 0 | 1.103s | 1.477s | 1.608s | 0.042s | 0.061s |
| messages | 10 | 30 | 30 | 0 | 1.361s | 1.709s | 1.967s | 0.045s | 0.054s |
| messages | 20 | 30 | 30 | 0 | 1.618s | 2.103s | 2.409s | 0.053s | 0.096s |

Result:

- accepted up to concurrency `20`
- p95 total latency stayed around `2.2s`
- p95 first chunk latency stayed below `100ms`
- no failures observed

## Long Output Baseline

Inputs:

- prompt source: inline
- prompt characters: `124`
- paths: `chat,responses,messages`
- concurrency levels: `1,5,10`
- requests per level: `10`

The first long-output run observed one `chat` outlier at `66.411s` for `concurrency=1`.
The second run did not reproduce that outlier. It is currently treated as upstream generation
variance rather than a gateway-systemic issue.

Second-run results:

| Path | Concurrency | Requests | Success | Failure | P50 total | P95 total | Max total | P50 first chunk | P95 first chunk |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| chat | 1 | 10 | 10 | 0 | 19.137s | 19.177s | 19.177s | 0.028s | 0.030s |
| chat | 5 | 10 | 10 | 0 | 28.152s | 28.165s | 28.165s | 0.051s | 0.064s |
| chat | 10 | 10 | 10 | 0 | 34.663s | 34.663s | 34.663s | 0.060s | 0.061s |
| responses | 1 | 10 | 10 | 0 | 19.137s | 19.165s | 19.165s | 0.008s | 0.027s |
| responses | 5 | 10 | 10 | 0 | 28.066s | 28.149s | 28.149s | 0.018s | 0.024s |
| responses | 10 | 10 | 10 | 0 | 34.745s | 34.748s | 34.748s | 0.024s | 0.028s |
| messages | 1 | 10 | 10 | 0 | 19.136s | 19.143s | 19.143s | 0.028s | 0.031s |
| messages | 5 | 10 | 10 | 0 | 27.019s | 28.076s | 28.076s | 0.046s | 0.051s |
| messages | 10 | 10 | 10 | 0 | 34.912s | 34.913s | 34.913s | 0.057s | 0.058s |

Result:

- accepted up to concurrency `10`
- p95 total latency stayed around `35s`
- p95 first chunk latency stayed below `70ms`
- no failures observed

## Large Context Baseline

Inputs:

- prompt source: `large-context-prompt.txt`
- prompt characters: `143200`
- prompt token estimate: roughly `35k-45k` tokens, tokenizer-dependent
- observed prompt tokens: not captured in the original run artifacts
- paths: `chat,responses,messages`
- concurrency levels: `1,3,5`
- requests per level: `10`

| Path | Concurrency | Requests | Success | Failure | P50 total | P95 total | Max total | P50 first chunk | P95 first chunk |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| chat | 1 | 10 | 10 | 0 | 7.533s | 9.375s | 9.375s | 0.109s | 1.959s |
| chat | 3 | 10 | 10 | 0 | 9.381s | 9.400s | 9.400s | 0.202s | 0.238s |
| chat | 5 | 10 | 10 | 0 | 10.007s | 10.048s | 10.048s | 0.285s | 0.323s |
| responses | 1 | 10 | 10 | 0 | 7.536s | 8.584s | 8.584s | 0.053s | 0.058s |
| responses | 3 | 10 | 10 | 0 | 9.373s | 9.394s | 9.394s | 0.123s | 0.136s |
| responses | 5 | 10 | 10 | 0 | 10.010s | 10.039s | 10.039s | 0.205s | 0.213s |
| messages | 1 | 10 | 10 | 0 | 7.535s | 7.540s | 7.540s | 0.109s | 0.121s |
| messages | 3 | 10 | 10 | 0 | 9.363s | 9.388s | 9.388s | 0.198s | 0.239s |
| messages | 5 | 10 | 10 | 0 | 10.014s | 10.026s | 10.026s | 0.291s | 0.316s |

Result:

- accepted up to concurrency `5`
- p95 total latency stayed around `10s`
- p95 first chunk latency stayed below `350ms` at concurrency `5`
- no failures observed

## Mixed-Model Concurrency Baseline

This baseline validates production-like concurrent traffic across two active models:

- `gpt-oss-120b-local`
- `qwen3.6-35b-a3b`

The goal is to verify that gateway routing, streaming pass-through, HTTP client pooling, and
metrics paths remain stable when multiple model routes are active at the same time.

### Mixed Normal Output

Inputs:

- mode: `mixed-model`
- prompt source: inline
- prompt characters: `54`
- paths: `chat,responses,messages`
- requests per level: `30`
- `gpt-oss-120b-local` concurrency: `10`
- `qwen3.6-35b-a3b` concurrency: `5`

| Model | Path | Concurrency | Requests | Success | Failure | P50 total | P95 total | Max total | P50 first chunk | P95 first chunk |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| gpt-oss-120b-local | chat | 10 | 30 | 30 | 0 | 0.512s | 0.668s | 0.703s | 0.038s | 0.053s |
| qwen3.6-35b-a3b | chat | 5 | 30 | 30 | 0 | 2.991s | 6.741s | 7.275s | 0.116s | 0.970s |
| gpt-oss-120b-local | responses | 10 | 30 | 30 | 0 | 0.480s | 0.600s | 0.602s | 0.026s | 0.037s |
| qwen3.6-35b-a3b | responses | 5 | 30 | 30 | 0 | 2.249s | 5.438s | 5.744s | 0.014s | 0.045s |
| gpt-oss-120b-local | messages | 10 | 30 | 30 | 0 | 0.571s | 0.754s | 0.767s | 0.042s | 0.053s |
| qwen3.6-35b-a3b | messages | 5 | 30 | 30 | 0 | 2.440s | 3.932s | 4.223s | 0.110s | 0.292s |

Result:

- accepted with `gpt-oss-120b-local` concurrency `10` and `qwen3.6-35b-a3b` concurrency `5`
- all model/path combinations completed with `100%` success
- `qwen3.6-35b-a3b` was slower than `gpt-oss-120b-local`, but without gateway failures

### Mixed Long Output

Inputs:

- mode: `mixed-model`
- prompt source: inline
- prompt characters: `124`
- paths: `chat,responses,messages`
- requests per level: `10`
- `gpt-oss-120b-local` concurrency: `5`
- `qwen3.6-35b-a3b` concurrency: `3`

| Model | Path | Concurrency | Requests | Success | Failure | P50 total | P95 total | Max total | P50 first chunk | P95 first chunk |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| gpt-oss-120b-local | chat | 5 | 10 | 10 | 0 | 13.677s | 13.732s | 13.732s | 0.048s | 0.052s |
| qwen3.6-35b-a3b | chat | 3 | 10 | 10 | 0 | 14.183s | 14.296s | 14.296s | 0.114s | 0.200s |
| gpt-oss-120b-local | responses | 5 | 10 | 10 | 0 | 13.247s | 13.712s | 13.712s | 0.018s | 0.022s |
| qwen3.6-35b-a3b | responses | 3 | 10 | 10 | 0 | 14.153s | 14.165s | 14.165s | 0.012s | 0.020s |
| gpt-oss-120b-local | messages | 5 | 10 | 10 | 0 | 14.230s | 21.857s | 21.857s | 0.055s | 0.104s |
| qwen3.6-35b-a3b | messages | 3 | 10 | 10 | 0 | 14.210s | 14.221s | 14.221s | 0.079s | 0.148s |

Result:

- accepted with `gpt-oss-120b-local` concurrency `5` and `qwen3.6-35b-a3b` concurrency `3`
- all model/path combinations completed with `100%` success
- `gpt-oss-120b-local` messages observed one p95/max value at `21.857s`, but without failures

### Mixed Large Context

Inputs:

- mode: `mixed-model`
- prompt source: `large-context-prompt.txt`
- prompt characters: `143200`
- prompt token estimate: roughly `35k-45k` tokens, tokenizer-dependent
- observed prompt tokens: not captured in the original run artifacts
- paths: `chat,responses,messages`
- requests per level: `10`
- `gpt-oss-120b-local` concurrency: `3`
- `qwen3.6-35b-a3b` concurrency: `2`

| Model | Path | Concurrency | Requests | Success | Failure | P50 total | P95 total | Max total | P50 first chunk | P95 first chunk |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| gpt-oss-120b-local | chat | 3 | 10 | 10 | 0 | 10.368s | 11.411s | 11.411s | 0.229s | 2.016s |
| qwen3.6-35b-a3b | chat | 2 | 10 | 10 | 0 | 7.028s | 8.849s | 8.849s | 0.262s | 2.081s |
| gpt-oss-120b-local | responses | 3 | 10 | 10 | 0 | 9.354s | 9.376s | 9.376s | 0.126s | 0.150s |
| qwen3.6-35b-a3b | responses | 2 | 10 | 10 | 0 | 7.028s | 7.071s | 7.071s | 0.168s | 0.229s |
| gpt-oss-120b-local | messages | 3 | 10 | 10 | 0 | 12.266s | 13.073s | 13.073s | 0.216s | 0.255s |
| qwen3.6-35b-a3b | messages | 2 | 10 | 10 | 0 | 7.031s | 7.148s | 7.148s | 0.262s | 0.333s |

Result:

- accepted with `gpt-oss-120b-local` concurrency `3` and `qwen3.6-35b-a3b` concurrency `2`
- all model/path combinations completed with `100%` success
- chat first chunk p95 was around `2s`, while responses/messages remained lower

## Interpretation

The current results suggest that gateway proxying, streaming pass-through, and bounded request
handling are stable for the tested production profiles.

Latency behavior appears to be dominated by upstream/model behavior:

- normal output stays low-latency across all three API paths
- long output total latency increases with concurrency, consistent with shared generation capacity
- large context latency reflects prompt processing cost and remains stable through concurrency `5`
- mixed-model traffic remains stable when two production model routes are active at the same time
- large-context prompt size should be compared by observed prompt tokens in future refreshes, not
  only by character count

The three API paths behaved consistently enough that no path-specific gateway regression is evident
from this baseline.

## Operational Evidence

The capacity baseline should be read together with operational checks from the same deployment
window.

Evidence recorded by the baseline tool:

- per-request status, first-chunk latency, total latency, received bytes, and received chunks
- `metrics_before.prom`
- `metrics_after.prom`
- per-profile `summary.md` and `summary.json`

Required operator checks for accepting or refreshing this baseline:

- `/livez` remains healthy after the run
- `/readyz` remains healthy after the run
- gateway container does not restart during the run
- gateway-origin failures remain at zero or are explicitly explained
- CPU and memory do not show continuous growth during the run
- token accounting status matches upstream usage behavior

Observed operational evidence for this accepted baseline:

- all completed single-model and mixed-model profiles reported `100%` request success
- no gateway-origin request failures were reported in the captured summaries
- `/v1/chat/completions`, `/v1/responses`, and `/v1/messages` all completed successfully in the
  production profiles
- production `/v1/messages` streaming token accounting and tool-use behavior were validated before
  this baseline was accepted
- production `/v1/responses` behavior and tool-use parity were validated before this baseline was
  accepted

Evidence not preserved in the original run artifacts:

- exact `/livez` and `/readyz` response snapshots after each profile
- container restart counter snapshot
- CPU and memory time series during each profile
- observed prompt-token values for the large-context prompt

The current accepted baseline assumes the missing operational checks above did not reveal blocking
issues during the production run. Future refreshes should preserve these snapshots alongside the
latency summaries. If any of these signals regress in a future run, update this document with the
observed failure mode instead of only updating the latency tables.

## Accepted Baseline

Accepted baseline for the current production model mix:

- normal output: accepted up to concurrency `20`, p95 total about `2.2s`
- long output: accepted up to concurrency `10`, p95 total about `35s`
- large context: accepted up to concurrency `5` with a `143,200`-character prompt, p95 total about `10s`
- mixed normal output: accepted with `gpt-oss-120b-local:10` and `qwen3.6-35b-a3b:5`
- mixed long output: accepted with `gpt-oss-120b-local:5` and `qwen3.6-35b-a3b:3`
- mixed large context: accepted with `gpt-oss-120b-local:3` and `qwen3.6-35b-a3b:2`

Operational notes:

- this baseline assumes the current single-process / single-worker deployment
- this baseline is streaming-only and does not establish a separate non-streaming capacity profile
- this baseline should be re-run after material model, vLLM, gateway, or topology changes
- this baseline should be paired with `/livez`, `/readyz`, container resource, and gateway metrics checks
- future large-context refreshes should record observed prompt tokens from gateway or upstream usage
  metrics in addition to prompt characters

## Remaining Work

Not yet covered by this document:

- maximum throughput benchmarking
- multi-worker or horizontally scaled shared-state behavior

## Same-Model Multi-Upstream Validation Checklist

This section defines the remaining validation work for model pools with more than one upstream
serving the same public model name.

Round-robin validation status:

- same-model multi-upstream round-robin behavior has been validated in production
- this capacity document does not keep the raw round-robin evidence

Connect-stage failover validation requires:

- same-model connect-stage failover behavior has been validated in production
- use the dedicated validation flow in
  [same-model-routing-validation-playbook.md](docs/same-model-routing-validation-playbook.md)
  when this validation must be repeated after topology or host-shape changes
- keep confirming upstream non-2xx responses and already-started streaming responses are not treated
  as generic retry cases

These checks are intentionally separate from capacity baseline testing because they validate routing
correctness and failure handling, not steady-state user-facing capacity.
