"""CLI Evaluation Pipeline for Companion Agent (Section 7.5: FR-4.1, FR-4.2 + Simulator Parity).

Capabilities:
  - Layer 0 Cross-Turn `PayloadIntegrityValidator`:
      * Detects missing `answerRecord` on `NEEDS_CONFIRMATION` tool calls (`[PAYLOAD_ANOMALY]`)
      * Detects illegal backward `answerRecord` state transitions from terminal states (`[STATE_MACHINE_BUG]`)
      * Detects unconfirmed HITL tool execution bypasses (`[ENGINE_BUG] UNCONFIRMED_EXECUTION_BYPASS`)
      * Detects empty `knowledgeSources` citation blocks (`[CITATION_ANOMALY]`)
  - Low-Signal Utterance Negative Suppression (`_LOW_SIGNAL_UTTERANCE_RE`) & Cumulative RAG Grounding carry-forward
  - Session-Level **Strict (Turn-Exact)** vs. **Forgiving (Session-Level)** `Precision / Recall / F1`
    with nested JSON flattening (`a.b.0.c`), `carryover` memory tracking, and `ignore_keys` filtering
  - Baseline Run Regression Delta Comparison (`--baseline-run`)
  - Ground-Truth Dataset Bootstrapper (`bootstrap_eval_dataset` / `aa-devkit bootstrap-eval`)
"""

from __future__ import annotations

import copy
import datetime
import json
from pathlib import Path
import re
import time
from typing import Any, Optional

from aa_devkit.client import CompanionAgentRestClient

# Low-signal utterance regex for automatic negative suppression checks
_LOW_SIGNAL_UTTERANCE_RE = re.compile(
    r"^\s*(?:(?:hi|hello|hey|there|good\s+(?:morning|afternoon|evening)|thanks|thank\s+you|"
    r"ok|okay|sure|got\s+it|alright|uh\s*huh|mm\s*hmm|yep|yeah|yes|no\s+problem|"
    r"one\s+moment(?:\s+please)?|hold\s+on(?:\s+please)?|let\s+me\s+check(?:\s+that)?)"
    r"[\s,!?.]*)+$",
    re.IGNORECASE,
)

DEFAULT_IGNORE_ENTITY_KEYS = frozenset({"tool_description", "_extraction_reasoning"})

TERMINAL_TOOL_STATES = frozenset(
    {
        "COMPLETED",
        "CALLED",
        "OUTPUT_GENERATED",
        "REJECTED",
        "CANCELLED",
        "CANCELED",
        "FAILED",
        "ERROR",
    }
)


class PayloadIntegrityValidator:
    """Layer 0 Cross-Turn State-Machine & Payload Contract Validator.

    Tracks `answerRecord` tool states across all turns of a conversation to catch:
      - Missing `answerRecord` on `NEEDS_CONFIRMATION` tool calls (`[PAYLOAD_ANOMALY]`)
      - Backward transitions from terminal states to `NEEDS_CONFIRMATION` (`[STATE_MACHINE_BUG]`)
      - Unconfirmed HITL execution bypasses (`[ENGINE_BUG] UNCONFIRMED_EXECUTION_BYPASS`)
      - Empty `knowledgeSources` citation blocks (`[CITATION_ANOMALY]`)
    """

    def __init__(self, confirmation_required_tools: Optional[set[str]] = None) -> None:
        self._tool_states_by_ar: dict[str, tuple[int, str, str]] = {}
        self._confirmed_ars: set[str] = set()
        self._confirmation_required_tools: set[str] = {
            t.strip() for t in (confirmation_required_tools or set()) if t and t.strip()
        }

    def record_confirmed_answer_record(self, answer_record: str) -> None:
        if answer_record:
            self._confirmed_ars.add(answer_record)

    def validate_turn(self, turn_id: int, resp: dict[str, Any]) -> list[str]:
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
                anomalies.append(f"[PAYLOAD_ANOMALY] API SuggestionResult error: {err_msg}")

            comp = r.get("generateCompanionSuggestionsResponse") or {}
            sugg = comp.get("companionSuggestion") or {}

            # Check knowledgeSources citations
            for ks in sugg.get("knowledgeSources") or []:
                if isinstance(ks, dict):
                    snippets = ks.get("snippets") or []
                    if not snippets and not ks.get("uri") and not ks.get("title"):
                        anomalies.append(
                            f"[CITATION_ANOMALY] Turn {turn_id}: knowledgeSource has empty snippets and no URI/title"
                        )

            for g in sugg.get("guidances") or []:
                if not isinstance(g, dict):
                    continue
                for tc_entry in g.get("toolCalls") or []:
                    tci = tc_entry.get("toolCallInfo") or tc_entry
                    tc = tci.get("toolCall") or {}
                    t_name = (
                        tc.get("toolDisplayName")
                        or tc.get("action")
                        or (tc.get("tool") or "").split("/")[-1]
                    )
                    t_state = str(tc.get("state") or tc.get("callState") or "").upper()
                    ar = str(tc.get("answerRecord") or tci.get("answerRecord") or "").strip()

                    if not t_name:
                        anomalies.append(f"[PAYLOAD_ANOMALY] Turn {turn_id}: ToolCall is missing tool name/action")
                        continue

                    if t_state == "NEEDS_CONFIRMATION":
                        if not ar:
                            anomalies.append(
                                f"[PAYLOAD_ANOMALY] Tool '{t_name}' in NEEDS_CONFIRMATION missing answerRecord"
                            )
                        else:
                            # Mark that this answerRecord was properly offered for confirmation
                            self._confirmed_ars.add(ar)

                    if ar and ar in self._tool_states_by_ar:
                        prev_turn, prev_tool, prev_state = self._tool_states_by_ar[ar]
                        if prev_state in TERMINAL_TOOL_STATES and t_state == "NEEDS_CONFIRMATION":
                            anomalies.append(
                                f"[STATE_MACHINE_BUG] Turn {turn_id}: answerRecord '{ar}' ({t_name}) "
                                f"transitioned backward from terminal state '{prev_state}' (Turn {prev_turn}) "
                                f"to 'NEEDS_CONFIRMATION'"
                            )

                    if (
                        t_name in self._confirmation_required_tools
                        and t_state in ("CALLED", "COMPLETED", "OUTPUT_GENERATED")
                        and (not ar or ar not in self._confirmed_ars)
                    ):
                        anomalies.append(
                            f"[ENGINE_BUG] UNCONFIRMED_EXECUTION_BYPASS: Tool '{t_name}' "
                            f"(confirmationRequirement=REQUIRED) transitioned to '{t_state}' on Turn {turn_id} "
                            f"without prior NEEDS_CONFIRMATION approval"
                        )

                    if ar and t_state:
                        self._tool_states_by_ar[ar] = (turn_id, t_name, t_state)

        return anomalies


def extract_confirmation_required_tools_from_bundle(bundle: Optional[dict[str, Any]]) -> set[str]:
    """Extracts the set of tool names with `confirmationRequirement == 'REQUIRED'` from an exported bundle."""
    if not bundle or not isinstance(bundle, dict):
        return set()
    req_tools: set[str] = set()
    for t in bundle.get("tools") or []:
        if not isinstance(t, dict):
            continue
        if str(t.get("confirmationRequirement") or "").upper() == "REQUIRED":
            for candidate in (
                t.get("displayName"),
                t.get("action"),
                (t.get("name") or "").split("/")[-1],
            ):
                if candidate:
                    req_tools.add(str(candidate))
    return req_tools


def extract_turn_grounding_snippets(resp: dict[str, Any]) -> list[str]:
    """Extracts RAG grounding snippets and titles from an AnalyzeContentResponse for cumulative carry-forward."""
    snippets: list[str] = []
    results = (
        resp.get("humanAgentSuggestionResults")
        or resp.get("endUserSuggestionResults")
        or []
    )
    for r in results:
        if not isinstance(r, dict):
            continue
        comp = r.get("generateCompanionSuggestionsResponse") or {}
        sugg = comp.get("companionSuggestion") or {}
        for ks in sugg.get("knowledgeSources") or []:
            if not isinstance(ks, dict):
                continue
            title = ks.get("title") or ks.get("uri") or "KnowledgeSource"
            for sn in ks.get("snippets") or []:
                txt = sn.get("text") if isinstance(sn, dict) else str(sn)
                if txt:
                    entry = f"[{title}] {txt.strip()}"
                    if entry not in snippets:
                        snippets.append(entry)
    return snippets


def flatten_nested_entities(
    obj: Any,
    parent_key: str = "",
    ignore_keys: frozenset[str] = DEFAULT_IGNORE_ENTITY_KEYS,
) -> dict[str, str]:
    """Flattens arbitrarily nested dicts and lists into dot-separated key paths (`a.b.0.c`)."""
    items: dict[str, str] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ignore_keys:
                continue
            new_key = f"{parent_key}.{k}" if parent_key else str(k)
            if isinstance(v, (dict, list)):
                items.update(flatten_nested_entities(v, new_key, ignore_keys))
            elif v is not None and str(v).strip() != "":
                items[new_key] = str(v).strip()
    elif isinstance(obj, list):
        for idx, v in enumerate(obj):
            new_key = f"{parent_key}.{idx}" if parent_key else str(idx)
            if isinstance(v, (dict, list)):
                items.update(flatten_nested_entities(v, new_key, ignore_keys))
            elif v is not None and str(v).strip() != "":
                items[new_key] = str(v).strip()
    return items


def _extract_observed_from_analyze_response(
    resp: dict[str, Any],
    validator: Optional[PayloadIntegrityValidator] = None,
    turn_id: int = 1,
) -> dict[str, Any]:
    """Extracts triggered guidance cards, tools, parameters, and Layer 0 anomalies from an AnalyzeContentResponse."""
    cards: list[str] = []
    tools: list[str] = []
    entities: dict[str, Any] = {}
    anomalies: list[str] = []

    if validator is not None:
        anomalies.extend(validator.validate_turn(turn_id, resp))

    results = (
        resp.get("humanAgentSuggestionResults")
        or resp.get("endUserSuggestionResults")
        or []
    )
    for r in results:
        if not isinstance(r, dict):
            continue
        if validator is None and isinstance(r.get("error"), dict):
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
                if validator is None and t_state == "NEEDS_CONFIRMATION" and not tc.get("answerRecord"):
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
        "grounding_snippets": extract_turn_grounding_snippets(resp),
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
    validator: Optional[PayloadIntegrityValidator] = None,
    cumulative_grounding: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Scores a single turn's AnalyzeContentResponse against `turn['expected']`."""
    expected = turn.get("expected") or {}
    turn_id = int(turn.get("turn_id") or 1)
    obs = _extract_observed_from_analyze_response(
        analyze_response, validator=validator, turn_id=turn_id
    )

    if cumulative_grounding is not None:
        for sn in obs["grounding_snippets"]:
            if sn not in cumulative_grounding:
                cumulative_grounding.append(sn)

    failures: list[str] = list(obs["anomalies"])
    text_str = str(turn.get("text") or "").strip()
    has_positive_expectations = bool(
        expected.get("expected_guidance_cards")
        or expected.get("expected_tools")
        or expected.get("expected_entities")
    )
    if "must_suppress" in expected:
        must_suppress = bool(expected.get("must_suppress"))
    else:
        must_suppress = bool(
            not has_positive_expectations
            and len(text_str.split()) <= 6
            and _LOW_SIGNAL_UTTERANCE_RE.match(text_str)
        )

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

        flat_obs = flatten_nested_entities(obs["entities"])
        flat_exp = flatten_nested_entities(expected.get("expected_entities") or {})
        for k, exp_v in flat_exp.items():
            # Support both exact top-level key and flattened nested key
            actual_val = flat_obs.get(k, obs["entities"].get(k))
            if actual_val is None:
                failures.append(f"Missed expected tool parameter '{k}'='{exp_v}'")
            elif _norm_val(actual_val) != _norm_val(exp_v):
                failures.append(
                    f"Mismatched tool parameter '{k}': expected '{exp_v}', got '{actual_val}'"
                )

        for k, forb_v in (expected.get("not_expected_entities") or {}).items():
            actual_val = flat_obs.get(k, obs["entities"].get(k))
            if actual_val is not None:
                if not forb_v or _norm_val(actual_val) == _norm_val(forb_v):
                    failures.append(
                        f"Forbidden tool parameter '{k}' was emitted with '{actual_val}'"
                    )

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
        "cumulative_rag_grounding_count": len(cumulative_grounding) if cumulative_grounding is not None else len(obs["grounding_snippets"]),
        "failures": failures,
        "latency_ms": round(latency_ms, 1),
    }


def calculate_strict_and_forgiving_metrics(
    conversations: list[dict[str, Any]],
    conversation_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Calculates both Strict (Turn-Exact) and Forgiving (Session-Level) Precision, Recall, F1, and Accuracy.

    - **Strict (Turn-Exact)**: Evaluates entity extraction and negative expectations on the exact turn
      where they are specified, while crediting `carryover` entities remembered in `ai_memory` from earlier turns.
    - **Forgiving (Session-Level)**: Evaluates whether all expected entities across the entire session
      were extracted at any point during the conversation without triggering forbidden entities.
    """
    strict_tp = 0
    strict_fp = 0
    strict_fn = 0
    strict_tn = 0
    strict_carryover = 0
    strict_extra = 0

    forgiving_tp = 0
    forgiving_fp = 0
    forgiving_fn = 0
    forgiving_tn = 0

    for conv, conv_res in zip(conversations, conversation_results):
        turns = conv.get("turns") or []
        verdicts = conv_res.get("turns") or []
        ai_memory: dict[str, str] = {}

        session_expected: dict[str, str] = {}
        session_forbidden: dict[str, str] = {}
        session_observed: dict[str, str] = {}

        for turn, tv in zip(turns, verdicts):
            exp_block = turn.get("expected") or {}
            flat_exp = flatten_nested_entities(exp_block.get("expected_entities") or {})
            flat_forb = {
                str(k): str(v) if v is not None else ""
                for k, v in (exp_block.get("not_expected_entities") or {}).items()
            }
            flat_obs = flatten_nested_entities(tv.get("observed_entities") or {})

            session_expected.update(flat_exp)
            session_forbidden.update(flat_forb)
            session_observed.update(flat_obs)

            # Evaluate expected entities for this turn (Strict)
            for k, exp_v in flat_exp.items():
                if k in flat_obs:
                    if _norm_val(flat_obs[k]) == _norm_val(exp_v):
                        strict_tp += 1
                    else:
                        strict_fp += 1
                        strict_fn += 1
                elif k in ai_memory and _norm_val(ai_memory[k]) == _norm_val(exp_v):
                    strict_tp += 1
                    strict_carryover += 1
                else:
                    strict_fn += 1

            # Evaluate forbidden (`not_expected_entities`) for this turn (Strict)
            for k, forb_v in flat_forb.items():
                if k in flat_obs and (not forb_v or _norm_val(flat_obs[k]) == _norm_val(forb_v)):
                    strict_fp += 1
                else:
                    strict_tn += 1

            # Count extra observed entities not in `flat_exp` or `ai_memory`
            for k, obs_v in flat_obs.items():
                if k not in flat_exp and k not in flat_forb:
                    if k in ai_memory and _norm_val(ai_memory[k]) == _norm_val(obs_v):
                        strict_carryover += 1
                    else:
                        strict_extra += 1

            ai_memory.update(flat_obs)

        # Session-level Forgiving tally
        for k, exp_v in session_expected.items():
            if k in session_observed and _norm_val(session_observed[k]) == _norm_val(exp_v):
                forgiving_tp += 1
            elif k in session_observed:
                forgiving_fp += 1
                forgiving_fn += 1
            else:
                forgiving_fn += 1

        for k, forb_v in session_forbidden.items():
            if k in session_observed and (not forb_v or _norm_val(session_observed[k]) == _norm_val(forb_v)):
                forgiving_fp += 1
            else:
                forgiving_tn += 1

    def _compute_rates(tp: int, fp: int, fn: int, tn: int) -> dict[str, Any]:
        precision = round((tp / (tp + fp)) * 100.0, 1) if (tp + fp) > 0 else 100.0
        recall = round((tp / (tp + fn)) * 100.0, 1) if (tp + fn) > 0 else 100.0
        f1 = (
            round((2 * precision * recall) / (precision + recall), 1)
            if (precision + recall) > 0
            else 0.0
        )
        total = tp + fp + fn + tn
        accuracy = round(((tp + tn) / total) * 100.0, 1) if total > 0 else 100.0
        return {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "tn": tn,
            "precision_pct": precision,
            "recall_pct": recall,
            "f1_pct": f1,
            "accuracy_pct": accuracy,
        }

    strict_metrics = _compute_rates(strict_tp, strict_fp, strict_fn, strict_tn)
    strict_metrics["carryover"] = strict_carryover
    strict_metrics["extra"] = strict_extra

    forgiving_metrics = _compute_rates(forgiving_tp, forgiving_fp, forgiving_fn, forgiving_tn)

    return {
        "strict": strict_metrics,
        "forgiving": forgiving_metrics,
    }


def compare_with_baseline_run(
    current_report: dict[str, Any],
    baseline_report: dict[str, Any],
) -> dict[str, Any]:
    """Computes regression deltas between `current_report` and a prior `baseline_report`."""
    curr_s = current_report.get("summary") or {}
    base_s = baseline_report.get("summary") or {}

    curr_pass = float(curr_s.get("turn_pass_rate_pct", 0.0))
    base_pass = float(base_s.get("turn_pass_rate_pct", 0.0))
    curr_supp = float(curr_s.get("negative_suppression_rate_pct", 0.0))
    base_supp = float(base_s.get("negative_suppression_rate_pct", 0.0))

    curr_strict_f1 = float(((curr_s.get("entity_metrics") or {}).get("strict") or {}).get("f1_pct", 0.0))
    base_strict_f1 = float(((base_s.get("entity_metrics") or {}).get("strict") or {}).get("f1_pct", 0.0))

    curr_forg_f1 = float(((curr_s.get("entity_metrics") or {}).get("forgiving") or {}).get("f1_pct", 0.0))
    base_forg_f1 = float(((base_s.get("entity_metrics") or {}).get("forgiving") or {}).get("f1_pct", 0.0))

    base_turns_by_key: dict[tuple[str, Any], bool] = {}
    for c in baseline_report.get("conversations") or []:
        cid = c.get("conversation_id") or ""
        for t in c.get("turns") or []:
            base_turns_by_key[(cid, t.get("turn_id"))] = bool(t.get("passed"))

    regressed_turns: list[str] = []
    improved_turns: list[str] = []
    for c in current_report.get("conversations") or []:
        cid = c.get("conversation_id") or ""
        for t in c.get("turns") or []:
            tid = t.get("turn_id")
            now_ok = bool(t.get("passed"))
            was_ok = base_turns_by_key.get((cid, tid))
            if was_ok is True and not now_ok:
                regressed_turns.append(f"{cid}#turn-{tid}")
            elif was_ok is False and now_ok:
                improved_turns.append(f"{cid}#turn-{tid}")

    return {
        "turn_pass_rate_delta_pct": round(curr_pass - base_pass, 1),
        "negative_suppression_delta_pct": round(curr_supp - base_supp, 1),
        "strict_f1_delta_pct": round(curr_strict_f1 - base_strict_f1, 1),
        "forgiving_f1_delta_pct": round(curr_forg_f1 - base_forg_f1, 1),
        "regressed_turns": regressed_turns,
        "improved_turns": improved_turns,
    }


def evaluate_dataset(
    dataset_path: str | Path,
    client: Optional[CompanionAgentRestClient] = None,
    profile_name: Optional[str] = None,
    bundle: Optional[dict[str, Any]] = None,
    baseline_run_path: Optional[str | Path] = None,
) -> dict[str, Any]:
    """Evaluates a turn-by-turn dataset either offline (from `actual_response`) or live via REST."""
    data = json.loads(Path(dataset_path).expanduser().resolve().read_text(encoding="utf-8"))
    conversations = data.get("conversations") or []
    conf_req_tools = extract_confirmation_required_tools_from_bundle(bundle)

    conversation_results: list[dict[str, Any]] = []
    total_turns = 0
    passed_turns = 0
    suppression_turns = 0
    suppression_passed = 0

    for conv in conversations:
        conv_id = conv.get("conversation_id") or "conv"
        turns = conv.get("turns") or []
        turn_verdicts: list[dict[str, Any]] = []
        validator = PayloadIntegrityValidator(confirmation_required_tools=conf_req_tools)
        cumulative_grounding: list[str] = []

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

            verdict = score_single_turn(
                turn,
                resp,
                latency_ms=elapsed_ms,
                validator=validator,
                cumulative_grounding=cumulative_grounding,
            )
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
    entity_metrics = calculate_strict_and_forgiving_metrics(conversations, conversation_results)

    report: dict[str, Any] = {
        "summary": {
            "total_conversations": len(conversations),
            "total_turns": total_turns,
            "passed_turns": passed_turns,
            "failed_turns": total_turns - passed_turns,
            "turn_pass_rate_pct": pass_rate,
            "negative_suppression_rate_pct": supp_rate,
            "entity_metrics": entity_metrics,
        },
        "conversations": conversation_results,
    }

    if baseline_run_path:
        baseline_data = json.loads(
            Path(baseline_run_path).expanduser().resolve().read_text(encoding="utf-8")
        )
        # Support either a saved eval report or a dataset path
        if "summary" not in baseline_data and "conversations" in baseline_data:
            baseline_data = evaluate_dataset(baseline_run_path, bundle=bundle)
        report["baseline_comparison"] = compare_with_baseline_run(report, baseline_data)

    return report


def bootstrap_eval_dataset(dataset_path: str | Path) -> dict[str, Any]:
    """Reverse-engineers ground-truth `expected` annotations from recorded `actual_response` payloads.

    Computes turn-over-turn entity deltas so each turn's `expected_entities` only requires
    newly extracted parameters (preventing duplicate carryover requirements), while capturing
    observed guidance cards, tools, and negative-suppression turns (`aa-devkit bootstrap-eval`).
    """
    raw = json.loads(Path(dataset_path).expanduser().resolve().read_text(encoding="utf-8"))
    bootstrapped = copy.deepcopy(raw)
    turns_bootstrapped = 0

    for conv in bootstrapped.get("conversations") or []:
        seen_entities: dict[str, Any] = {}
        for turn in conv.get("turns") or []:
            resp = turn.get("actual_response") or {}
            obs = _extract_observed_from_analyze_response(resp)

            delta_entities: dict[str, Any] = {}
            for k, v in obs["entities"].items():
                if k in DEFAULT_IGNORE_ENTITY_KEYS:
                    continue
                if k not in seen_entities or _norm_val(seen_entities[k]) != _norm_val(v):
                    delta_entities[k] = v
                    seen_entities[k] = v

            is_silent = len(obs["cards"]) == 0 and len(obs["tools"]) == 0 and len(delta_entities) == 0
            existing_exp = turn.get("expected") or {}
            turn["expected"] = {
                "must_suppress": is_silent,
                "expected_guidance_cards": list(obs["cards"]),
                "expected_tools": list(obs["tools"]),
                "expected_entities": delta_entities,
                "not_expected_entities": existing_exp.get("not_expected_entities") or {},
            }
            turns_bootstrapped += 1

    bootstrapped["bootstrapMetadata"] = {
        "bootstrappedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "turnsBootstrapped": turns_bootstrapped,
    }
    return bootstrapped


def format_eval_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    em = s.get("entity_metrics") or {}
    strict_m = em.get("strict") or {}
    forg_m = em.get("forgiving") or {}

    lines = [
        "# Companion Agent Evaluation Report (FR-4.1)",
        "",
        f"- **Conversations Evaluated**: {s['total_conversations']}",
        f"- **Turn Pass Rate**: **{s['turn_pass_rate_pct']}%** ({s['passed_turns']}/{s['total_turns']} turns)",
        f"- **Negative Suppression Rate**: **{s['negative_suppression_rate_pct']}%**",
    ]
    if strict_m:
        lines.append(
            f"- **Strict (Turn-Exact) Entity F1**: **{strict_m.get('f1_pct', 100.0)}%** "
            f"(Precision: {strict_m.get('precision_pct', 100.0)}%, Recall: {strict_m.get('recall_pct', 100.0)}%, "
            f"TP={strict_m.get('tp', 0)}, FP={strict_m.get('fp', 0)}, FN={strict_m.get('fn', 0)}, "
            f"Carryover={strict_m.get('carryover', 0)})"
        )
    if forg_m:
        lines.append(
            f"- **Forgiving (Session-Level) Entity F1**: **{forg_m.get('f1_pct', 100.0)}%** "
            f"(Precision: {forg_m.get('precision_pct', 100.0)}%, Recall: {forg_m.get('recall_pct', 100.0)}%, "
            f"TP={forg_m.get('tp', 0)}, FP={forg_m.get('fp', 0)}, FN={forg_m.get('fn', 0)})"
        )

    base_cmp = report.get("baseline_comparison")
    if base_cmp:
        lines.extend(
            [
                "",
                "## Baseline Regression Delta",
                f"- **Turn Pass Rate Delta**: `{base_cmp['turn_pass_rate_delta_pct']:+.1f}%`",
                f"- **Negative Suppression Delta**: `{base_cmp['negative_suppression_delta_pct']:+.1f}%`",
                f"- **Strict F1 Delta**: `{base_cmp['strict_f1_delta_pct']:+.1f}%`",
                f"- **Forgiving F1 Delta**: `{base_cmp['forgiving_f1_delta_pct']:+.1f}%`",
                f"- **Regressed Turns**: {base_cmp['regressed_turns'] or 'None'}",
                f"- **Improved Turns**: {base_cmp['improved_turns'] or 'None'}",
            ]
        )

    lines.append("")
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
