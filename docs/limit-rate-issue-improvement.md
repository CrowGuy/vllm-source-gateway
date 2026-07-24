你的現象其實很典型：**少數 coding agent 使用者把系統從「互動式 LLM」打成「批次推理平台」**。如果某個 peak 是 700+ requests、4000 多萬 tokens，平均就是每 request 約 5.7 萬 tokens。這不是一般 chat 使用量，通常代表長 context、反覆 tool loop、agent retry、批次掃 repo、或多 agent 並行。

我的技術決策會是：**先用 Gateway 做公平性和上限防守，再用 vLLM 調 scheduler/KV/cache，最後用 observability 把黑箱部門變成可治理的 workload。** 不先防守的話，vLLM 調參只是在幫暴衝流量吃更多資源。

**1. vLLM 設定調整**
優先看這幾個 vLLM 指標：`vllm:num_requests_waiting`、`vllm:request_queue_time_seconds`、`vllm:time_to_first_token_seconds`、`vllm:request_prefill_time_seconds`、`vllm:request_decode_time_seconds`、`vllm:kv_cache_usage_perc`、`vllm:num_preemptions_total`。vLLM 官方 metrics 本來就有 running/waiting、queue time、TTFT、prefill/decode latency、KV cache usage 這些訊號。

建議調整方向：

- **限制有效併發**：調整 `max_num_seqs`，不要讓太多長 context coding-agent request 同時進 scheduler。  
  為什麼：高併發不等於高吞吐；超過 KV/cache/scheduler 甜蜜點後，p95/p99 latency 會爆炸。

- **控制 batch token 大小**：調整 `max_num_batched_tokens`。  
  為什麼：太大會提高吞吐但傷害 TTFT；太小會降低 GPU 利用率。Gemma 現在問題偏 tail latency，所以要找「p95 可接受」而不是「極限吞吐」點。

- **開啟或確認 chunked prefill / partial prefill**：針對長 prompt workload，確認 `enable_chunked_prefill`、`max_num_partial_prefills`、`long_prefill_token_threshold` 這類設定。  
  為什麼：coding agent 常帶大量 repo/context，prefill 會堵住短請求；chunked prefill 可以降低長 prompt 對互動請求的霸佔。

- **限制 context / output**：降低 Gemma 對外允許的 `max_model_len` 或 gateway 層限制超長 request，並限制 `max_tokens`。  
  為什麼：4000 萬 tokens peak 不是靠 scheduler 能完全解決，必須把單 request 成本封頂。

- **保守調 `gpu_memory_utilization`**：若 KV cache 壓力高且沒有 OOM 風險，可小幅提高；若 preemption 或 OOM 風險高，反而要降低併發或 context。  
  為什麼：KV cache 滿了之後會進入等待/重算/preemption，使用者看到的就是「沒反應」。

**2. Gateway 防守**
這是我會最優先做的，因為你已經知道有一兩個部門異常高用量，而且他們不願說明用途。

- **per-department + per-model concurrency limit**  
  例如 Gemma 對單一部門最多同時 2-4 個 active requests，整體 Gemma 最多 N 個 active requests。超過就 `429` 或短暫 bounded queue。  
  為什麼：沒有公平性時，單一部門可以把所有 GPU queue 塞滿，其他部門就一起被拖死。

- **per-department token budget window**  
  例如每部門每 5 分鐘、每 1 小時有 prompt/generation token budget；超過後降級、延遲或拒絕。  
  為什麼：requests 數不夠，coding agent 的成本主要在 tokens。你看到 4000 萬 tokens，應該用 token budget 管，而不是只看 request count。

- **限制 request shape**  
  Gateway 可以拒絕或改寫不合理參數：`n > 1`、過大的 `max_tokens`、過大的 request body、超長 context、非 streaming 長輸出。  
  為什麼：coding agent 常見 fan-out 和 retry，一個 API call 可能實際要求多份 generation。

- **分 API key，不再只分 department**  
  要求那兩個部門把 coding agent 使用改用專用 key，例如 `finance_coding_agent`、`data_platform_coding_agent`，Prometheus label 仍可映射成 bounded `department` 或新增 bounded `workload_class`。  
  為什麼：他們不說用途沒關係，但系統要能把「人類互動」和「agent 批次」拆開治理。

- **錯誤要快，不要慢死**  
  超過限制回 `429`，附 `Retry-After`；不要讓所有 request 在 upstream 排到 120 秒 timeout。  
  為什麼：快速失敗比全體 latency 爆炸更可控，agent 也比較容易退避。

**3. Usage Observability**
你現在看的 `requests by department` 和 `tokens by department and model` 已經抓到問題，但還不夠做治理。

我會補這些視角：

- **tokens/request by department + model**  
  用 `tokens increase / requests increase` 看誰在送超大 context 或超長輸出。  
  為什麼：700 requests、4000 萬 tokens這種 peak，平均 tokens/request 才是關鍵訊號。

- **prompt vs generation 分開看**  
  prompt tokens 高：可能是 repo dump、長 context、RAG-like stuffing。  
  generation tokens 高：可能是 agent loop、長 code output、retry。  
  為什麼：兩者優化方式不同，不能只看 total tokens。

- **Gateway 新增 in-flight 與 upstream latency**  
  建議 metrics：
  - `gateway_inflight_requests{department,model_name,endpoint}`：phase 1 已完成。
  - `gateway_upstream_request_duration_seconds{model_name,upstream_name,endpoint}`：phase 2 已完成；第一版量測 non-streaming full upstream POST duration。
  - `gateway_stream_first_chunk_seconds{department,model_name,endpoint}`：phase 2 已完成；第一版量測 streaming upstream first non-empty chunk latency。
  - `gateway_upstream_selections_total{model_name,upstream_name}`：phase 2 已完成。

  `tokens/request` 不需要 gateway 新 counter，可由 dashboard 用既有
  `gateway_prompt_tokens_total`、`gateway_generation_tokens_total` 和 request counter 推導。

  為什麼：vLLM 告訴你 upstream 是否排隊；gateway 告訴你是哪個部門、哪個 model、哪條路徑把壓力打進去。

- **alerts 要針對治理而不是只針對故障**
  例如：
  - 單部門 5 分鐘 tokens 超過平常 p95 的 3 倍
  - 單部門 Gemma requests rate 異常
  - Gemma `requests_waiting` 持續 > 閾值
  - Gemma queue time p95 > 1-3 秒
  - 部門 tokens/request 異常升高

  為什麼：這不是單純 downtime，而是 noisy-neighbor / capacity abuse 問題。

**我會採取的優先順序**
1. 先上 Gateway per-department/per-model concurrency limit 和 token budget。  
2. 同時補 observability：tokens/request、prompt/generation 拆分、Gemma queue/TTFT dashboard。  
3. 再根據觀測結果調 vLLM `max_num_seqs`、`max_num_batched_tokens`、chunked prefill、KV cache 相關設定。  
4. 要求異常部門使用專用 API key；不需要他們說明細節，但必須接受可量測、可限流、可審計。

核心理由很簡單：**你沒有更多 GPU，也無法隔離硬體，所以唯一可控資源就是 admission control、公平性、以及可觀測治理。** vLLM 調參能改善效率，但不能阻止一兩個部門把整個服務吃滿。
