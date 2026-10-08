# Companion Agent Latency Taxonomy & Observability Guide (`FR-6.1` – `FR-6.4`)

Use this reference when diagnosing Companion Agent turn latency, instrumenting customer integrations, or running `aa-devkit latency-report`.

---

## 1. Canonical 6-Stage Call Latency Taxonomy (`FR-6.1` & `FR-6.2`)

Every real-time Companion Agent voice or chat turn traverses up to six distinct latency stages. Distinguishing **client-side measurements** from **backend API metrics** prevents misattributing telephony/proxy buffering to LLM inference.

| Stage # | Span ID | Call Stage | Timing Source (`FR-6.2`) | Typical Healthy Range | Common Bottleneck Causes |
| :---: | :--- | :--- | :--- | :--- | :--- |
| **1** | `customer_integration_network` | **Customer Integration & Network** (SIPREC / gRPC / Proxy) | `CLIENT_SIDE` | `10–80 ms` | Oversized audio buffer chunks (`>200 ms`), proxy TLS re-handshakes, cross-region routing (e.g., `us-east1` telephony $\rightarrow$ `europe-west2` Dialogflow). |
| **2** | `ui_bridge` | **UI Module & Connector Dispatch** (`<agent-assist-companion-agent>`) | `CLIENT_SIDE` | `5–40 ms` | Missing duplicate-turn suppression (`<2500 ms`), excessive DOM re-renders, or double-dispatching `:analyzeContent` from both custom code and `UiModulesConnector`. |
| **3** | `speech_endpointing_stt` | **Speech Endpointing & Cloud STT Recognition** | `HYBRID_CLIENT_AND_API` | `150–450 ms` | High silence endpointing thresholds, `singleUtterance` misconfiguration, or non-telephony STT model selection (`chirp_2` / `useGeminiAsr` recommended). |
| **4** | `llm_generation_vertex` | **LLM Generation** (Vertex AI Guidance / Workflow Selection) | `API_METRIC_OR_CLIENT_FALLBACK` | `400–1200 ms` | Monolithic `GuidanceInstruction` cards, un-split `IF/ELSE` conditions, excessive `overarchingGuidance` token length, or missing `CUSTOMER_MESSAGE` trigger scoping. |
| **5** | `tool_execution` | **Tool Execution** (Datastore RAG / OpenAPI / CES Python / MCP) | `API_METRIC_OR_CLIENT_FALLBACK` | `100–900 ms` | Slow external customer REST endpoints (`openApiTool`), unindexed Datastore queries, or synchronous `proactiveEnabled: true` tools firing on every utterance. |
| **6** | `quota_throttling` | **Priority Throughput & Quota Throttling** | `API_RESPONSE_METRIC` | `0 ms` (target) | Project-level Vertex AI RPM/TPM saturation or missing Provisioned Throughput / Priority PayGo configuration. |

---

## 2. Consuming Backend Observability Metrics (`FR-6.3`)

When Dialogflow v2beta1 `:analyzeContent` or `humanAgentSuggestionResults[]` returns `observabilityMetrics` or `latencyMetrics`, the DevKit (`aa-devkit latency-report`) automatically merges backend spans over client-side fallback measurements:

```json
{
  "humanAgentSuggestionResults": [
    {
      "observabilityMetrics": {
        "speech_endpointing_stt": 240.0,
        "llm_generation_vertex": 580.0,
        "tool_execution": 195.0,
        "quota_throttling": 0.0
      }
    }
  ]
}
```

---

## 3. Generating Per-Call & Aggregate Latency Reports (`FR-6.4`)

Run the stateless CLI against any recorded evaluation dataset or telemetry capture JSON:

```bash
# Markdown table report (stdout or --output)
aa-devkit latency-report evals/samples/sample_eval_conversations.json

# Self-contained HTML visual dashboard
aa-devkit latency-report evals/samples/sample_eval_conversations.json \
  --format html \
  --output reports/latency_dashboard.html
```
