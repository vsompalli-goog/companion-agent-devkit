"""Eval-Driven Hill-Climbing & Pre-Run Cost Estimator (Section 7.8: FR-7.1, FR-7.2, FR-7.3).

Capabilities:
  - FR-7.2: Pre-run Vertex AI / Dialogflow token & USD cost estimator (`estimate_eval_cost`)
    displayed before executing live evaluation or hill-climbing iterations.
  - FR-7.1 & FR-7.3 (`PRIV-13`): Analyzes turn-by-turn evaluation failures (`analyze_loss_patterns`)
    and proposes concrete local JSON configuration improvements (`propose_bundle_improvements`)
    scoped strictly to the customer's local bundle and GCP project.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

# Default Gemini 2.5 Flash / Vertex AI Companion Agent ballpark pricing per 1M tokens (USD)
DEFAULT_INPUT_PRICE_PER_1M_USD = 0.15
DEFAULT_OUTPUT_PRICE_PER_1M_USD = 0.60


def estimate_eval_cost(
    dataset: dict[str, Any],
    local_bundle: dict[str, Any],
    iterations: int = 1,
    input_price_per_1m_usd: float = DEFAULT_INPUT_PRICE_PER_1M_USD,
    output_price_per_1m_usd: float = DEFAULT_OUTPUT_PRICE_PER_1M_USD,
) -> dict[str, Any]:
    """Estimates token consumption and Vertex AI cost BEFORE running live evals (FR-7.2)."""
    conversations = dataset.get("conversations") or []
    total_turns = sum(len(c.get("turns") or []) for c in conversations)

    # Estimate static prompt tokens from CompanionAgent configuration
    agent_json = json.dumps(local_bundle.get("companion_agent") or {})
    tools_json = json.dumps(local_bundle.get("tools") or [])
    static_config_chars = len(agent_json) + len(tools_json)
    static_config_tokens = max(250, static_config_chars // 4)

    avg_utterance_tokens = 45
    avg_history_tokens = 180
    avg_output_tokens_per_turn = 160

    input_tokens_per_turn = static_config_tokens + avg_utterance_tokens + avg_history_tokens
    total_input_tokens = input_tokens_per_turn * total_turns * max(1, iterations)
    total_output_tokens = avg_output_tokens_per_turn * total_turns * max(1, iterations)

    est_input_usd = (total_input_tokens / 1_000_000.0) * input_price_per_1m_usd
    est_output_usd = (total_output_tokens / 1_000_000.0) * output_price_per_1m_usd
    total_usd = round(est_input_usd + est_output_usd, 4)

    return {
        "conversations": len(conversations),
        "turns_per_iteration": total_turns,
        "iterations": max(1, iterations),
        "total_analyze_content_calls": total_turns * max(1, iterations),
        "estimated_input_tokens": total_input_tokens,
        "estimated_output_tokens": total_output_tokens,
        "estimated_cost_usd": total_usd,
        "pricing_assumptions": {
            "input_per_1m_tokens_usd": input_price_per_1m_usd,
            "output_per_1m_tokens_usd": output_price_per_1m_usd,
        },
    }


def analyze_loss_patterns(eval_report: dict[str, Any]) -> list[dict[str, Any]]:
    """Groups turn-by-turn evaluation failures into actionable loss patterns (FR-7.1)."""
    missed_cards: dict[str, list[str]] = {}
    missed_tools: dict[str, list[str]] = {}
    false_positive_turns: list[str] = []
    param_mismatches: list[str] = []

    for conv in eval_report.get("conversations") or []:
        cid = conv.get("conversation_id") or "conv"
        for tv in conv.get("turns") or []:
            if tv.get("passed"):
                continue
            tid = f"{cid}#turn-{tv.get('turn_id')}"
            for fail in tv.get("failures") or []:
                if "Expected negative suppression" in fail:
                    false_positive_turns.append(f"{tid}: {fail}")
                elif "Missed expected guidance card:" in fail:
                    # Extract card name between single quotes
                    parts = fail.split("'")
                    card_name = parts[1] if len(parts) > 1 else "Unknown Card"
                    missed_cards.setdefault(card_name, []).append(
                        f"{tid} ('{tv.get('text', '')}')"
                    )
                elif "Missed expected tool call:" in fail:
                    parts = fail.split("'")
                    tool_name = parts[1] if len(parts) > 1 else "Unknown Tool"
                    missed_tools.setdefault(tool_name, []).append(
                        f"{tid} ('{tv.get('text', '')}')"
                    )
                elif "parameter" in fail.lower():
                    param_mismatches.append(f"{tid}: {fail}")

    patterns: list[dict[str, Any]] = []
    if false_positive_turns:
        patterns.append(
            {
                "category": "FALSE_POSITIVE_TRIGGER",
                "count": len(false_positive_turns),
                "evidence": false_positive_turns,
                "recommendation": (
                    "Set `triggerEvent: CUSTOMER_MESSAGE` on intake/verification cards and tighten "
                    "generic conditions so greetings/acknowledgments remain silent."
                ),
            }
        )
    for card_name, occurrences in missed_cards.items():
        patterns.append(
            {
                "category": "MISSED_GUIDANCE_CARD",
                "target": card_name,
                "count": len(occurrences),
                "evidence": occurrences,
                "recommendation": (
                    f"Broaden `condition` on '{card_name}' with concrete customer intent phrasings "
                    "and remove overly restrictive multi-clause requirements."
                ),
            }
        )
    for tool_name, occurrences in missed_tools.items():
        patterns.append(
            {
                "category": "MISSED_TOOL_CALL",
                "target": tool_name,
                "count": len(occurrences),
                "evidence": occurrences,
                "recommendation": (
                    f"Ensure `{{@TOOL:{tool_name}}}` is explicitly bound inside the relevant card's `actions[]` "
                    f"and linked in `cesToolSpecs` with `proactiveEnabled: true`."
                ),
            }
        )
    if param_mismatches:
        patterns.append(
            {
                "category": "TOOL_PARAMETER_MISMATCH",
                "count": len(param_mismatches),
                "evidence": param_mismatches,
                "recommendation": (
                    "Clarify parameter formats and examples inside the CES tool `Args:` docstring and "
                    "avoid hallucinating default parameter values when missing from `<call_context>`."
                ),
            }
        )

    return patterns


def propose_bundle_improvements(
    local_bundle: dict[str, Any],
    loss_patterns: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    """Generates a candidate local bundle with deterministic hill-climbing fixes (FR-7.1, FR-7.3)."""
    candidate = copy.deepcopy(local_bundle)
    applied_proposals: list[str] = []

    agent = candidate.get("companion_agent")
    if not isinstance(agent, dict):
        return candidate, applied_proposals

    has_fp = any(p["category"] == "FALSE_POSITIVE_TRIGGER" for p in loss_patterns)
    missed_tool_names = [
        p["target"] for p in loss_patterns if p["category"] == "MISSED_TOOL_CALL" and p.get("target")
    ]

    for sc in agent.get("skillConfigs") or []:
        gsc = sc.get("guidanceSkillConfig") or {}
        for card in gsc.get("guidanceInstructions") or []:
            title = card.get("displayName") or "Card"
            if has_fp and not card.get("triggerEvent"):
                if any(
                    kw in title.lower()
                    for kw in ("verify", "verification", "intake", "authenticate", "greeting")
                ):
                    card["triggerEvent"] = "CUSTOMER_MESSAGE"
                    applied_proposals.append(
                        f"[{title}] Added `triggerEvent: CUSTOMER_MESSAGE` to eliminate human-agent greeting false positives."
                    )

            for t_name in missed_tool_names:
                actions = card.get("actions") or []
                action_texts = " ".join(str(a.get("description", "")) for a in actions)
                if t_name.lower() in title.lower() and f"{{@TOOL:{t_name}}}" not in action_texts:
                    actions.append(
                        {
                            "description": f"Invoke {{@TOOL:{t_name}}} using verified parameters from the conversation."
                        }
                    )
                    card["actions"] = actions
                    applied_proposals.append(
                        f"[{title}] Appended explicit `{{@TOOL:{t_name}}}` invocation step to `actions[]`."
                    )

    return candidate, applied_proposals


def format_hillclimb_markdown(
    cost_estimate: dict[str, Any],
    eval_report: dict[str, Any],
    loss_patterns: list[dict[str, Any]],
    applied_proposals: list[str],
    candidate_dir: str | Path | None = None,
) -> str:
    """Formats the L4 Hill-Climbing iteration report."""
    s = eval_report["summary"]
    lines = [
        "# Companion Agent Hill-Climbing Report (Section 7.8: FR-7.1 – FR-7.3)",
        "",
        "## 1. Pre-Run Vertex AI Cost Estimate (`FR-7.2`)",
        f"- **Conversations / Turns**: {cost_estimate['conversations']} conversations ({cost_estimate['turns_per_iteration']} turns/iteration x {cost_estimate['iterations']} iteration(s))",
        f"- **Estimated Tokens**: `{cost_estimate['estimated_input_tokens']:,}` input / `{cost_estimate['estimated_output_tokens']:,}` output",
        f"- **Estimated Vertex AI Cost**: **`${cost_estimate['estimated_cost_usd']:.4f} USD`**",
        "",
        "## 2. Evaluation Summary (`FR-7.1`)",
        f"- **Turn Pass Rate**: **{s['turn_pass_rate_pct']}%** ({s['passed_turns']}/{s['total_turns']} turns)",
        f"- **Negative Suppression Rate**: **{s['negative_suppression_rate_pct']}%**",
        "",
        "## 3. Identified Loss Patterns",
    ]
    if not loss_patterns:
        lines.append("- No loss patterns detected (100% pass rate).")
    else:
        for idx, pat in enumerate(loss_patterns, start=1):
            target_suffix = f" (`{pat['target']}`)" if pat.get("target") else ""
            lines.append(f"### {idx}. `{pat['category']}`{target_suffix} — {pat['count']} occurrence(s)")
            lines.append(f"- **Recommendation**: {pat['recommendation']}")
            for ev in (pat.get("evidence") or [])[:5]:
                lines.append(f"  - Evidence: `{ev}`")
            lines.append("")

    lines.append("## 4. Proposed Configuration Improvements (`FR-7.3` / `PRIV-13`)")
    if applied_proposals:
        for prop in applied_proposals:
            lines.append(f"- {prop}")
    else:
        lines.append("- Manual prompt/condition refinement recommended based on Section 3 loss patterns.")

    if candidate_dir:
        lines.extend(
            [
                "",
                f"Candidate bundle written to: `{candidate_dir}`",
                "Validate proposed changes with `aa-devkit review --fail-on-p0` and deploy through your Git/CI workflow.",
            ]
        )
    lines.append("")
    return "\n".join(lines)
