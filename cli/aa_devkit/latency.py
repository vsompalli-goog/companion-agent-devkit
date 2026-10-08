"""Latency Breakdown & Observability Engine (Section 7.7: FR-6.1, FR-6.2, FR-6.3, FR-6.4).

Defines the canonical 6-stage Companion Agent call latency taxonomy, documents whether
each span is collected via client-side measurement or API response metrics (`FR-6.2`),
merges API observability metrics when present (`FR-6.3`), and produces per-call and
aggregate latency breakdown reports (`FR-6.4`).
"""

from __future__ import annotations

import html
import json
import math
from pathlib import Path
from typing import Any

# Canonical Latency Taxonomy (FR-6.1 & FR-6.2)
LATENCY_TAXONOMY: list[dict[str, str]] = [
    {
        "span_id": "customer_integration_network",
        "stage_order": "1",
        "label": "Customer Integration & Network (SIPREC / gRPC / Proxy)",
        "measurement_source": "CLIENT_SIDE",
        "description": "Time spent in customer telephony/gRPC connector and network hop to Google Cloud.",
    },
    {
        "span_id": "ui_bridge",
        "stage_order": "2",
        "label": "UI Module & Connector Dispatch (<agent-assist-companion-agent>)",
        "measurement_source": "CLIENT_SIDE",
        "description": "Browser dispatch, deduplication cache, and DOM rendering overhead.",
    },
    {
        "span_id": "speech_endpointing_stt",
        "stage_order": "3",
        "label": "Speech Endpointing & Cloud STT Recognition",
        "measurement_source": "HYBRID_CLIENT_AND_API",
        "description": "Voice activity endpointing delay and Speech-to-Text recognition latency.",
    },
    {
        "span_id": "llm_generation_vertex",
        "stage_order": "4",
        "label": "LLM Generation (Vertex AI Guidance / Workflow Selection)",
        "measurement_source": "API_METRIC_OR_CLIENT_FALLBACK",
        "description": "Companion Agent LLM inference time on Vertex AI.",
    },
    {
        "span_id": "tool_execution",
        "stage_order": "5",
        "label": "Tool Execution (Datastore RAG / OpenAPI / CES Python / MCP)",
        "measurement_source": "API_METRIC_OR_CLIENT_FALLBACK",
        "description": "External or CES tool invocation latency during proactive or reactive turns.",
    },
    {
        "span_id": "quota_throttling",
        "stage_order": "6",
        "label": "Priority Throughput & Quota Throttling",
        "measurement_source": "API_RESPONSE_METRIC",
        "description": "Queueing or retry backoff due to Vertex AI / Dialogflow project quota limits.",
    },
]


def compute_percentiles(values: list[float]) -> dict[str, Any]:
    """Computes min, p50, p90, p95, p99, max, mean, and count for a list of latencies (ms)."""
    clean = sorted(
        float(v)
        for v in (values or [])
        if isinstance(v, (int, float)) and not math.isnan(float(v)) and float(v) >= 0.0
    )
    n = len(clean)
    if n == 0:
        return {"count": 0, "min": 0, "p50": 0, "p90": 0, "p95": 0, "p99": 0, "max": 0, "mean": 0}

    def _pct(p: float) -> int:
        if n == 1:
            return round(clean[0])
        pos = (p / 100.0) * (n - 1)
        lo = int(math.floor(pos))
        hi = int(math.ceil(pos))
        if lo == hi:
            return round(clean[lo])
        weight = pos - lo
        return round(clean[lo] * (1.0 - weight) + clean[hi] * weight)

    return {
        "count": n,
        "min": round(clean[0]),
        "p50": _pct(50),
        "p90": _pct(90),
        "p95": _pct(95),
        "p99": _pct(99),
        "max": round(clean[-1]),
        "mean": round(sum(clean) / n),
    }


def extract_api_observability_spans(analyze_response: dict[str, Any]) -> dict[str, float]:
    """Pulls backend observability metrics from AnalyzeContentResponse when present (FR-6.3)."""
    spans: dict[str, float] = {}
    if not isinstance(analyze_response, dict):
        return spans

    # Inspect optional observabilityMetrics / latencyMetrics on response or suggestion results
    obs = analyze_response.get("observabilityMetrics") or analyze_response.get("latencyMetrics") or {}
    if isinstance(obs, dict):
        for span_meta in LATENCY_TAXONOMY:
            sid = span_meta["span_id"]
            if isinstance(obs.get(sid), (int, float)):
                spans[sid] = float(obs[sid])

    for r in analyze_response.get("humanAgentSuggestionResults") or []:
        if not isinstance(r, dict):
            continue
        r_obs = r.get("observabilityMetrics") or r.get("latencyMetrics") or {}
        if isinstance(r_obs, dict):
            for span_meta in LATENCY_TAXONOMY:
                sid = span_meta["span_id"]
                if isinstance(r_obs.get(sid), (int, float)):
                    spans[sid] = float(r_obs[sid])
    return spans


def analyze_latency_dataset(data: dict[str, Any]) -> dict[str, Any]:
    """Builds per-call and aggregate latency breakdowns across all 6 taxonomy spans (FR-6.4)."""
    conversations = data.get("conversations") or []
    aggregate_by_span: dict[str, list[float]] = {m["span_id"]: [] for m in LATENCY_TAXONOMY}
    aggregate_e2e: list[float] = []
    per_call_summaries: list[dict[str, Any]] = []

    for conv in conversations:
        conv_id = conv.get("conversation_id") or "call"
        call_by_span: dict[str, list[float]] = {m["span_id"]: [] for m in LATENCY_TAXONOMY}
        call_e2e: list[float] = []

        for turn in conv.get("turns") or []:
            client_spans = dict(turn.get("latency_spans_ms") or {})
            api_spans = extract_api_observability_spans(turn.get("actual_response") or {})
            merged_spans = {**client_spans, **api_spans}

            turn_total = 0.0
            for span_meta in LATENCY_TAXONOMY:
                sid = span_meta["span_id"]
                val = merged_spans.get(sid)
                if isinstance(val, (int, float)) and val >= 0:
                    fval = float(val)
                    call_by_span[sid].append(fval)
                    aggregate_by_span[sid].append(fval)
                    turn_total += fval

            if turn_total > 0:
                call_e2e.append(turn_total)
                aggregate_e2e.append(turn_total)

        per_call_summaries.append(
            {
                "conversation_id": conv_id,
                "e2e_turn_ms": compute_percentiles(call_e2e),
                "spans": {sid: compute_percentiles(vals) for sid, vals in call_by_span.items()},
            }
        )

    return {
        "taxonomy": LATENCY_TAXONOMY,
        "aggregate": {
            "e2e_turn_ms": compute_percentiles(aggregate_e2e),
            "spans": {sid: compute_percentiles(vals) for sid, vals in aggregate_by_span.items()},
        },
        "calls": per_call_summaries,
    }


def format_latency_markdown(report: dict[str, Any]) -> str:
    """Formats the latency breakdown report into a Markdown dashboard table."""
    lines = [
        "# Companion Agent Latency Breakdown Report (FR-6.1 – FR-6.4)",
        "",
        "## 1. Aggregate Latency Distribution by Call Stage (ms)",
        "| Stage | Span ID | Source | Count | Min | p50 | p90 | p95 | p99 | Max | Mean |",
        "| :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]
    agg_spans = report["aggregate"]["spans"]
    for meta in report["taxonomy"]:
        sid = meta["span_id"]
        stats = agg_spans.get(sid) or {}
        lines.append(
            f"| {meta['stage_order']} | `{sid}` | `{meta['measurement_source']}` | "
            f"{stats.get('count', 0)} | {stats.get('min', 0)}ms | **{stats.get('p50', 0)}ms** | "
            f"{stats.get('p90', 0)}ms | {stats.get('p95', 0)}ms | **{stats.get('p99', 0)}ms** | "
            f"{stats.get('max', 0)}ms | {stats.get('mean', 0)}ms |"
        )

    e2e = report["aggregate"]["e2e_turn_ms"]
    lines.extend(
        [
            "",
            f"**Aggregate Turn End-to-End**: `p50={e2e['p50']}ms` | `p90={e2e['p90']}ms` | `p99={e2e['p99']}ms` (`count={e2e['count']}`)",
            "",
            "## 2. Per-Call Latency Breakdown",
        ]
    )
    for call in report.get("calls") or []:
        ce2e = call["e2e_turn_ms"]
        lines.append(
            f"- **`{call['conversation_id']}`**: Turn E2E `p50={ce2e['p50']}ms`, `p95={ce2e['p95']}ms`, `max={ce2e['max']}ms`"
        )
    lines.append("")
    return "\n".join(lines)


def format_latency_html_dashboard(report: dict[str, Any]) -> str:
    """Generates a self-contained HTML latency breakdown dashboard (FR-6.4)."""
    agg_spans = report["aggregate"]["spans"]
    rows_html = []
    for meta in report["taxonomy"]:
        sid = meta["span_id"]
        st = agg_spans.get(sid) or {}
        p50 = st.get("p50", 0)
        bar_width = min(100, int((p50 / 1000.0) * 100)) if p50 else 0
        rows_html.append(
            f"<tr>"
            f"<td>{html.escape(meta['stage_order'])}</td>"
            f"<td><code>{html.escape(sid)}</code><br><small>{html.escape(meta['label'])}</small></td>"
            f"<td><code>{html.escape(meta['measurement_source'])}</code></td>"
            f"<td>{st.get('count', 0)}</td>"
            f"<td><strong>{p50} ms</strong><div style='background:#1a73e8;height:6px;width:{bar_width}%;border-radius:3px;margin-top:4px;'></div></td>"
            f"<td>{st.get('p90', 0)} ms</td>"
            f"<td>{st.get('p95', 0)} ms</td>"
            f"<td><strong>{st.get('p99', 0)} ms</strong></td>"
            f"<td>{st.get('max', 0)} ms</td>"
            f"</tr>"
        )

    e2e = report["aggregate"]["e2e_turn_ms"]
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Companion Agent Latency Breakdown Dashboard</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 32px; color: #202124; background: #f8f9fa; }}
  .card {{ background: #fff; border: 1px solid #dadce0; border-radius: 12px; padding: 24px; margin-bottom: 24px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
  th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #e8eaed; font-size: 14px; }}
  th {{ background: #f1f3f4; font-weight: 600; }}
  code {{ background: #f1f3f4; padding: 2px 6px; border-radius: 4px; font-size: 12px; }}
</style>
</head>
<body>
  <div class="card">
    <h1>Companion Agent Latency Breakdown Dashboard</h1>
    <p>Aggregate Turn End-to-End: <strong>p50 = {e2e['p50']} ms</strong> | <strong>p90 = {e2e['p90']} ms</strong> | <strong>p99 = {e2e['p99']} ms</strong> ({e2e['count']} turns)</p>
    <table>
      <thead>
        <tr><th>#</th><th>Call Stage / Span</th><th>Timing Source</th><th>Count</th><th>p50</th><th>p90</th><th>p95</th><th>p99</th><th>Max</th></tr>
      </thead>
      <tbody>
        {"".join(rows_html)}
      </tbody>
    </table>
  </div>
</body>
</html>
"""
