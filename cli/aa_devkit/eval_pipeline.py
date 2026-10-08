"""CLI Evaluation Pipeline for Companion Agent (Section 7.5: FR-4.1, FR-4.2).

Runs a Companion Agent against standard turn-by-turn test conversations (either
live via Dialogflow v2beta1 `:analyzeContent` or offline against pre-recorded
`actual_response` payloads) and scores each turn against expected outputs:
  - Layer 0 Payload & AnswerRecord contract integrity
  - Guidance card precision/recall (`expected_guidance_cards`)
  - Tool trigger & argument accuracy (`expected_tools`, `expected_entities`, `not_expected_entities`)
  - Negative suppression on greeting/filler turns (`must_suppress`)
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
import time
from typing import Any, Optional

from aa_devkit.client import CompanionAgentRestClient


def _extract_observed_from_analyze_response(resp: dict[str, Any]) -> dict[str, Any]:
    """Extracts triggered guidance cards, tools, parameters, and errors from an AnalyzeContentResponse."""
    cards: list[str] = []
    tools: list[str] = []
    entities: dict[str, Any] = {}
    anomalies: list[str] = []

    results = (
        resp.get("humanAgentSuggestionResults")
        or resp.get("endUserSuggestionResults")
        or []
    )
    for r in results:
        if not isinstance(r, dict):
            continue
        if isinstance(r.get("error"), dict):
            err_msg = r["error"].get("message") or "SuggestionResult error"
            anomalies.append(f"API SuggestionResult error: {err_msg}")
        comp = r.get("generateCompanionSuggestionsResponse") or {}
        sugg = comp.get("companionSuggestion") or {}
        for g in sugg.get("guidances") or []:
            if not isinstance(g, dict):
                continue
            src = g.get("instructionSource") or {}
            card_title = src.get("displayName") or g.get("displayName") or ""
            if card_title and card_title not in cards:
                cards.append(card_title)
            for tc_entry in g.get("toolCalls") or []:
                tci = tc_entry.get("toolCallInfo") or tc_entry
                tc = tci.get("toolCall") or {}
                t_name = (
                    tc.get("toolDisplayName")
                    or tc.get("action")
                    or (tc.get("tool") or "").split("/")[-1]
                )
                if t_name and t_name not in tools:
                    tools.append(t_name)
                t_state = str(tc.get("state") or tc.get("callState") or "").upper()
                if t_state == "NEEDS_CONFIRMATION" and not tc.get("answerRecord"):
                    anomalies.append(f"Tool '{t_name}' in NEEDS_CONFIRMATION missing answerRecord")
                params = tc.get("inputParameters") or tc.get("input") or {}
                if isinstance(params, dict):
                    entities.update(params)

        wf_state = comp.get("workflowState") or {}
        for hist in wf_state.get("toolCallHistory") or []:
            tci = (hist.get("toolCall") or {}).get("toolCallInfo") or {}
            tc = tci.get("toolCall") or {}
            t_name = tc.get("toolDisplayName") or (tc.get("tool") or "").split("/")[-1]
            if t_name and t_name not in tools:
                tools.append(t_name)
            params = tc.get("inputParameters") or {}
            if isinstance(params, dict):
                entities.update(params)

    return {
        "cards": cards,
        "tools": tools,
        "entities": entities,
        "anomalies": anomalies,
    }


def _norm_val(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, (dict, list)):
        return json.dumps(val, sort_keys=True).lower().replace(" ", "")
    return str(val).strip().lower()


def score_single_turn(
    turn: dict[str, Any],
    analyze_response: dict[str, Any],
    latency_ms: float = 0.0,
) -> dict[str, Any]:
    """Scores a single turn's AnalyzeContentResponse against `turn['expected']`."""
    expected = turn.get("expected") or {}
    obs = _extract_observed_from_analyze_response(analyze_response)

    failures: list[str] = list(obs["anomalies"])
    must_suppress = bool(expected.get("must_suppress", False))

    if must_suppress:
        if obs["cards"] or obs["tools"]:
            failures.append(
                f"Expected negative suppression (silent turn), but triggered cards={obs['cards']} tools={obs['tools']}"
            )
    else:
        for exp_card in expected.get("expected_guidance_cards") or []:
            if not any(exp_card.lower() in c.lower() for c in obs["cards"]):
                failures.append(f"Missed expected guidance card: '{exp_card}' (got {obs['cards']})")

        for exp_tool in expected.get("expected_tools") or []:
            if not any(exp_tool.lower() == t.lower() for t in obs["tools"]):
                failures.append(f"Missed expected tool call: '{exp_tool}' (got {obs['tools']})")

        for k, exp_v in (expected.get("expected_entities") or {}).items():
            if k not in obs["entities"]:
                failures.append(f"Missed expected tool parameter '{k}'='{exp_v}'")
            elif _norm_val(obs["entities"][k]) != _norm_val(exp_v):
                failures.append(
                    f"Mismatched tool parameter '{k}': expected '{exp_v}', got '{obs['entities'][k]}'"
                )

        for k, forb_v in (expected.get("not_expected_entities") or {}).items():
            if k in obs["entities"]:
                if not forb_v or _norm_val(obs["entities"][k]) == _norm_val(forb_v):
                    failures.append(f"Forbidden tool parameter '{k}' was emitted with '{obs['entities'][k]}'")

    passed = len(failures) == 0
    return {
        "turn_id": turn.get("turn_id"),
        "role": turn.get("role"),
        "text": turn.get("text"),
        "passed": passed,
        "must_suppress": must_suppress,
        "observed_cards": obs["cards"],
        "observed_tools": obs["tools"],
        "observed_entities": obs["entities"],
        "failures": failures,
        "latency_ms": round(latency_ms, 1),
    }


def evaluate_dataset(
    dataset_path: str | Path,
    client: Optional[CompanionAgentRestClient] = None,
    profile_name: Optional[str] = None,
) -> dict[str, Any]:
    """Evaluates a turn-by-turn dataset either offline (from `actual_response`) or live via REST."""
    data = json.loads(Path(dataset_path).expanduser().resolve().read_text(encoding="utf-8"))
    conversations = data.get("conversations") or []

    conversation_results: list[dict[str, Any]] = []
    total_turns = 0
    passed_turns = 0
    suppression_turns = 0
    suppression_passed = 0

    for conv in conversations:
        conv_id = conv.get("conversation_id") or "conv"
        turns = conv.get("turns") or []
        turn_verdicts: list[dict[str, Any]] = []

        live_conv_name = ""
        end_user_part = ""
        human_agent_part = ""

        if client and profile_name:
            parent = f"projects/{client.project_id}/locations/{client.location}"
            conv_resp = client.request(
                "POST",
                f"v2beta1/{parent}/conversations",
                json_body={"conversationProfile": profile_name},
            )
            live_conv_name = conv_resp["name"]
            u_resp = client.request(
                "POST",
                f"v2beta1/{live_conv_name}/participants",
                json_body={"role": "END_USER"},
            )
            a_resp = client.request(
                "POST",
                f"v2beta1/{live_conv_name}/participants",
                json_body={"role": "HUMAN_AGENT"},
            )
            end_user_part = u_resp["name"]
            human_agent_part = a_resp["name"]

            ingested = conv.get("ingested_context") or {}
            if ingested:
                now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
                ctx_refs = {
                    k: {
                        "contextContents": [
                            {
                                "content": json.dumps(v),
                                "contentFormat": "JSON",
                                "ingestionTime": now_iso,
                            }
                        ],
                        "updateMode": "OVERWRITE",
                        "languageCode": "en-US",
                    }
                    for k, v in ingested.items()
                }
                client.request(
                    "POST",
                    f"v2beta1/{live_conv_name}:ingestContextReferences",
                    json_body={"contextReferences": ctx_refs},
                )

        for turn in turns:
            total_turns += 1
            t0 = time.perf_counter()
            if client and profile_name and live_conv_name:
                part = human_agent_part if turn.get("role") == "HUMAN_AGENT" else end_user_part
                resp = client.request(
                    "POST",
                    f"v2beta1/{part}:analyzeContent",
                    json_body={"textInput": {"text": turn.get("text", ""), "languageCode": "en-US"}},
                )
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
            else:
                resp = turn.get("actual_response") or {}
                spans = turn.get("latency_spans_ms") or {}
                elapsed_ms = float(sum(v for v in spans.values() if isinstance(v, (int, float))))

            verdict = score_single_turn(turn, resp, latency_ms=elapsed_ms)
            turn_verdicts.append(verdict)
            if verdict["passed"]:
                passed_turns += 1
            if verdict["must_suppress"]:
                suppression_turns += 1
                if verdict["passed"]:
                    suppression_passed += 1

        conversation_results.append(
            {
                "conversation_id": conv_id,
                "passed": all(v["passed"] for v in turn_verdicts),
                "turns": turn_verdicts,
            }
        )

    pass_rate = round((passed_turns / max(1, total_turns)) * 100.0, 1)
    supp_rate = (
        round((suppression_passed / suppression_turns) * 100.0, 1)
        if suppression_turns > 0
        else 100.0
    )

    return {
        "summary": {
            "total_conversations": len(conversations),
            "total_turns": total_turns,
            "passed_turns": passed_turns,
            "failed_turns": total_turns - passed_turns,
            "turn_pass_rate_pct": pass_rate,
            "negative_suppression_rate_pct": supp_rate,
        },
        "conversations": conversation_results,
    }


def format_eval_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        "# Companion Agent Evaluation Report (FR-4.1)",
        "",
        f"- **Conversations Evaluated**: {s['total_conversations']}",
        f"- **Turn Pass Rate**: **{s['turn_pass_rate_pct']}%** ({s['passed_turns']}/{s['total_turns']} turns)",
        f"- **Negative Suppression Rate**: **{s['negative_suppression_rate_pct']}%**",
        "",
    ]
    for conv in report.get("conversations") or []:
        status = "PASS" if conv["passed"] else "FAIL"
        lines.append(f"## Conversation `{conv['conversation_id']}` — {status}")
        for tv in conv.get("turns") or []:
            icon = "PASS" if tv["passed"] else "FAIL"
            lines.append(
                f"- **Turn #{tv['turn_id']} ({tv['role']})** [{icon}, {tv['latency_ms']}ms]: `{tv['text']}`"
            )
            for fail in tv.get("failures") or []:
                lines.append(f"  - Failure: {fail}")
        lines.append("")
    return "\n".join(lines)
