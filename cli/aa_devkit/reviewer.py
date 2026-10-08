"""Deterministic best-practice configuration reviewer (FR-2.2).

Audits an exported Companion Agent configuration bundle (directory or `.zip`)
against Google Cloud Companion Agent backend engineering rules and returns
prioritized P0 / P1 / P2 recommendations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any

LEGACY_TOOL_RE = re.compile(r"\{\$tool\.([A-Za-z0-9_.-]+)\}")
MODERN_TOOL_RE = re.compile(r"\{@TOOL:([^}]+)\}")
DEF_FUNC_RE = re.compile(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)", re.MULTILINE)
MUTATION_PREFIXES = (
    "cancel_",
    "refund_",
    "update_",
    "delete_",
    "create_",
    "issue_",
    "waive_",
    "submit_",
    "modify_",
    "reset_",
    "close_",
)
NEGATIVE_START_RE = re.compile(
    r"^\s*(?:do\s+not|don't|never|avoid|refrain\s+from)\b", re.IGNORECASE
)
FORMATTING_ONLY_RE = re.compile(
    r"^\s*(?:keep\s+(?:your\s+)?(?:response|reply|answer|message)s?\s+under|"
    r"limit\s+(?:your\s+)?(?:response|reply)s?\s+to|"
    r"always\s+use\s+a\s+(?:warm|friendly|empathetic|concise)\s+tone)\b",
    re.IGNORECASE,
)
BRANCHING_RE = re.compile(
    r"\b(?:if\s+[^.]{3,120},?\s+(?:then\s+)?[^.]{3,120};\s*otherwise\b|"
    r"if\s+[^.]{3,120}\.\s*otherwise\s*,|"
    r"\belse\s+if\b|"
    r"\botherwise,\s+(?:ask|invoke|tell|suggest|do)\b)",
    re.IGNORECASE,
)
NEGATIVE_TRIGGER_RE = re.compile(
    r"\b(?:do\s+not\s+(?:suggest|trigger|apply|use)|"
    r"don't\s+(?:suggest|trigger|apply)|"
    r"never\s+(?:suggest|trigger)|"
    r"not\s+during\s+(?:initial\s+)?greetings?)\b",
    re.IGNORECASE,
)
DISPLAY_DETAILS_LOGIC_RE = re.compile(
    r"(?:\{@TOOL:|\{\$tool\.|<call_context>|CUSTOMERCONTEXT|"
    r"\b(?:must\s+not|do\s+not|never|only\s+if|unless|when\s+the\s+customer)\b)",
    re.IGNORECASE,
)
INTAKE_TITLE_RE = re.compile(
    r"\b(?:verification|verify|authentication|authenticate|intake|greeting|welcome|identify)\b",
    re.IGNORECASE,
)


@dataclass
class ReviewFinding:
    rule_id: str
    severity: str  # "P0" | "P1" | "P2"
    resource: str
    title: str
    detail: str
    recommendation: str


def _collect_known_tool_identifiers(
    agent: dict[str, Any], tools: list[dict[str, Any]]
) -> set[str]:
    identifiers: set[str] = set()
    for spec in agent.get("cesToolSpecs") or []:
        if isinstance(spec, dict) and spec.get("cesTool"):
            path = str(spec["cesTool"]).strip()
            identifiers.add(path)
            identifiers.add(path.split("/")[-1])
    for t_path in agent.get("tools") or []:
        if isinstance(t_path, str) and t_path.strip():
            identifiers.add(t_path.strip())
            identifiers.add(t_path.strip().split("/")[-1])
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        for key in ("name", "shortName", "displayName", "toolKey"):
            val = tool.get(key)
            if isinstance(val, str) and val.strip():
                identifiers.add(val.strip())
                identifiers.add(val.strip().split("/")[-1])
        py_fn = tool.get("pythonFunction") or {}
        if isinstance(py_fn, dict) and py_fn.get("name"):
            identifiers.add(str(py_fn["name"]).strip())
    return identifiers


def review_bundle(bundle: dict[str, Any]) -> list[ReviewFinding]:
    """Runs all deterministic engineering best-practice checks on a loaded bundle."""
    findings: list[ReviewFinding] = []
    profile = bundle.get("conversation_profile") or {}
    agent = bundle.get("companion_agent") or {}
    workflows = bundle.get("workflows") or []
    tools = bundle.get("tools") or []

    known_tools = _collect_known_tool_identifiers(agent, tools)

    # 1. Inspect CompanionAgent GuidanceSkillConfig
    for sc_idx, sc in enumerate(agent.get("skillConfigs") or []):
        if not isinstance(sc, dict):
            continue
        skill_trigger = sc.get("skillTriggeringEvent") or "END_OF_UTTERANCE"
        gsc = sc.get("guidanceSkillConfig") or {}
        overarching = str(gsc.get("overarchingGuidance") or "")

        # CA-P2-001: overarchingGuidance 4-section hygiene
        if overarching.strip():
            lower_og = overarching.lower()
            missing_sections = []
            if "role" not in lower_og and "persona" not in lower_og:
                missing_sections.append("Role & Persona")
            if "style" not in lower_og and "brevity" not in lower_og and "sentence" not in lower_og:
                missing_sections.append("Channel Style & Brevity")
            if "priority" not in lower_og and "hierarchy" not in lower_og:
                missing_sections.append("Priority Hierarchy")
            if "guardrail" not in lower_og and "completion" not in lower_og:
                missing_sections.append("Global Completion & Guardrails")

            if missing_sections:
                findings.append(
                    ReviewFinding(
                        rule_id="CA-P2-001",
                        severity="P2",
                        resource=f"companion_agent.skillConfigs[{sc_idx}].overarchingGuidance",
                        title="overarchingGuidance missing recommended 4-section structure",
                        detail=f"Missing explicit sections for: {', '.join(missing_sections)}.",
                        recommendation=(
                            "Structure overarchingGuidance into 4 concise sections: "
                            "(1) Role & Persona, (2) Channel Style & Brevity, "
                            "(3) Priority Hierarchy, and (4) Global Completion & Guardrails."
                        ),
                    )
                )
            if MODERN_TOOL_RE.search(overarching) or LEGACY_TOOL_RE.search(overarching):
                findings.append(
                    ReviewFinding(
                        rule_id="CA-P2-001",
                        severity="P2",
                        resource=f"companion_agent.skillConfigs[{sc_idx}].overarchingGuidance",
                        title="Tool bindings inside overarchingGuidance",
                        detail="overarchingGuidance contains direct tool bindings.",
                        recommendation=(
                            "Keep overarchingGuidance limited to global persona, style, priority, and guardrails. "
                            "Move {@TOOL:...} invocations into specific GuidanceInstruction actions[]."
                        ),
                    )
                )

        instructions = gsc.get("guidanceInstructions") or []
        for gi_idx, gi in enumerate(instructions):
            if not isinstance(gi, dict):
                continue
            title = str(gi.get("displayName") or f"Instruction #{gi_idx + 1}")
            res_label = f"GuidanceInstruction['{title}']"
            display_details = str(gi.get("displayDetails") or "")
            condition = str(gi.get("condition") or "")
            card_trigger = gi.get("triggerEvent") or skill_trigger
            actions = gi.get("actions") or []

            # CA-P0-002: displayDetails UI-only visibility leak
            if display_details and (
                DISPLAY_DETAILS_LOGIC_RE.search(display_details) or len(display_details) > 220
            ):
                findings.append(
                    ReviewFinding(
                        rule_id="CA-P0-002",
                        severity="P0",
                        resource=f"{res_label}.displayDetails",
                        title="Trigger logic or tool rules placed in UI-only displayDetails",
                        detail=(
                            "`displayDetails` is rendered ONLY in the UI card header and is NEVER sent to the LLM. "
                            f"Found logic/rules in displayDetails: '{display_details[:120]}...'"
                        ),
                        recommendation=(
                            "Keep displayDetails to a 1-sentence human-facing summary. Move all trigger conditions, "
                            "exclusions, and {@TOOL:...} references into `condition` or `actions[].description`."
                        ),
                    )
                )

            # CA-P0-001: Legacy {$tool.x} or unlinked {@TOOL:x}
            texts_to_scan = [condition] + [
                str(a.get("description") or "") for a in actions if isinstance(a, dict)
            ]
            combined_card_text = "\n".join(texts_to_scan)

            for match in LEGACY_TOOL_RE.finditer(combined_card_text):
                findings.append(
                    ReviewFinding(
                        rule_id="CA-P0-001",
                        severity="P0",
                        resource=res_label,
                        title=f"Legacy tool syntax '{{$tool.{match.group(1)}}}' will not bind",
                        detail=f"Found legacy tool binding `{{$tool.{match.group(1)}}}` in `{title}`.",
                        recommendation=(
                            f"Replace `{{$tool.{match.group(1)}}}` with `{{@TOOL:{match.group(1)}}}` "
                            "and verify the tool is linked in `cesToolSpecs`."
                        ),
                    )
                )

            for match in MODERN_TOOL_RE.finditer(combined_card_text):
                tool_ref = match.group(1).strip()
                tool_short = tool_ref.split("/")[-1]
                if tool_ref not in known_tools and tool_short not in known_tools:
                    findings.append(
                        ReviewFinding(
                            rule_id="CA-P0-001",
                            severity="P0",
                            resource=res_label,
                            title=f"Unlinked tool reference '{{@TOOL:{tool_ref}}}'",
                            detail=(
                                f"`{title}` references `{{@TOOL:{tool_ref}}}`, which is not linked in "
                                "`companion_agent.cesToolSpecs` or `tools`."
                            ),
                            recommendation=(
                                f"Add `{tool_ref}` to `cesToolSpecs` (with `proactiveEnabled: true`) "
                                "or update the tool name to match an existing linked tool."
                            ),
                        )
                    )

            # CA-P0-003: Checkpoint Rule Stall
            for act_idx, act in enumerate(actions):
                if not isinstance(act, dict):
                    continue
                desc = str(act.get("description") or "").strip()
                if not desc:
                    continue
                is_negative_only = bool(NEGATIVE_START_RE.match(desc)) and "{@TOOL:" not in desc
                is_formatting_only = bool(FORMATTING_ONLY_RE.match(desc)) and "{@TOOL:" not in desc
                # Check if it lacks any positive imperative verb like ask/identify/invoke/confirm/provide/suggest
                has_positive_checkpoint = bool(
                    re.search(
                        r"\b(?:ask|identify|invoke|call|confirm|summarize|provide|suggest|collect|tell)\b",
                        desc,
                        re.IGNORECASE,
                    )
                )
                if (is_negative_only and not has_positive_checkpoint) or is_formatting_only:
                    findings.append(
                        ReviewFinding(
                            rule_id="CA-P0-003",
                            severity="P0",
                            resource=f"{res_label}.actions[{act_idx}]",
                            title="Standalone negative or formatting action violates Checkpoint Rule",
                            detail=(
                                f"Action #{act_idx + 1} (`{desc[:100]}`) isolates a negative or formatting rule "
                                "with no observable transcript/tool checkpoint."
                            ),
                            recommendation=(
                                "Never isolate `Do NOT...` or brevity rules in their own `actions[]` step — "
                                "`completed_actions` cannot observe a non-event, permanently stalling subsequent steps. "
                                "Fold this constraint into the positive action step it modifies."
                            ),
                        )
                    )

                # CA-P1-001: IF / ELSE branching inside a single card
                if BRANCHING_RE.search(desc):
                    findings.append(
                        ReviewFinding(
                            rule_id="CA-P1-001",
                            severity="P1",
                            resource=f"{res_label}.actions[{act_idx}]",
                            title="Conditional IF/ELSE branching inside a single Guidance card",
                            detail=(
                                f"Action #{act_idx + 1} contains conditional branching (`{desc[:100]}...`). "
                                "Untaken branches remain incomplete in `completed_actions`, keeping the card stuck."
                            ),
                            recommendation=(
                                "Follow 'One Scenario = One Card': split mutually exclusive paths into separate "
                                "`GuidanceInstruction` cards with mutually exclusive `condition` definitions."
                            ),
                        )
                    )

            # CA-P1-002: Negative trigger words & missing CUSTOMER_MESSAGE on intake cards
            if NEGATIVE_TRIGGER_RE.search(condition) or NEGATIVE_TRIGGER_RE.search(title):
                findings.append(
                    ReviewFinding(
                        rule_id="CA-P1-002",
                        severity="P1",
                        resource=f"{res_label}.condition",
                        title="Negative trigger phrasing increases Turn-1 false positives",
                        detail=(
                            f"`{title}` uses negative trigger phrasing (`Do NOT suggest/trigger...`). "
                            "Semantic retrieval matches the negated keywords and increases false positives."
                        ),
                        recommendation=(
                            "Rewrite `condition` to describe positively what the customer IS saying or requesting."
                        ),
                    )
                )

            if (
                INTAKE_TITLE_RE.search(title)
                and card_trigger == "END_OF_UTTERANCE"
                and not gi.get("triggerEvent")
            ):
                findings.append(
                    ReviewFinding(
                        rule_id="CA-P1-002",
                        severity="P1",
                        resource=f"{res_label}.triggerEvent",
                        title="Intake/Verification card inherits END_OF_UTTERANCE without CUSTOMER_MESSAGE override",
                        detail=(
                            f"`{title}` evaluates on `END_OF_UTTERANCE`. On voice calls where the human agent speaks "
                            "first, this card can fire prematurely on the agent's Turn-1 greeting."
                        ),
                        recommendation=(
                            'Set `"triggerEvent": "CUSTOMER_MESSAGE"` on customer-driven intake/verification cards.'
                        ),
                    )
                )

            # CA-P1-003: <call_context> fail-safe & attractor checks
            if re.search(r"call\s*context|<call_context>|CUSTOMERCONTEXT", condition, re.IGNORECASE):
                if re.search(r"\b(?:is\s+missing|is\s+absent|not\s+present)\b", condition, re.IGNORECASE) and (
                    "DEFAULT" not in condition
                ):
                    findings.append(
                        ReviewFinding(
                            rule_id="CA-P1-003",
                            severity="P1",
                            resource=f"{res_label}.condition",
                            title="Context condition gates on absence without Pattern B DEFAULT/EXCEPTION structure",
                            detail=(
                                "When no context is ingested, `<call_context>` is completely empty (even the "
                                "`### CUSTOMERCONTEXT` header is absent). Gating on absence alone is brittle."
                            ),
                            recommendation=(
                                "Use Pattern B: designate the safeguard-enforcing card as `DEFAULT` (explicitly naming "
                                "empty/missing context) and the safeguard-skipping card as `EXCEPTION` (`Apply ONLY if...`)."
                            ),
                        )
                    )
                if "EXCEPTION" in condition.upper():
                    for act_idx, act in enumerate(actions):
                        desc = str((act or {}).get("description") or "")
                        if re.search(r"preferred\s+name\s+from\s+the\s+call\s+context", desc, re.IGNORECASE):
                            findings.append(
                                ReviewFinding(
                                    rule_id="CA-P1-003",
                                    severity="P1",
                                    resource=f"{res_label}.actions[{act_idx}]",
                                    title="Attractor phrase inside EXCEPTION card action",
                                    detail=(
                                        f"`{title}` is an EXCEPTION card whose action mentions 'preferred name from "
                                        "the call context', which acts as a lexical magnet when callers state their name."
                                    ),
                                    recommendation=(
                                        "Remove attractor phrases from Exception card actions so unverified callers "
                                        "stating their name on Turn 1 do not trigger the Exception card."
                                    ),
                                )
                            )

    # 2. Inspect Workflows
    for wf in workflows:
        if not isinstance(wf, dict):
            continue
        wf_name = wf.get("displayName") or wf.get("name") or "Workflow"
        wf_desc = str(wf.get("description") or "")
        if NEGATIVE_TRIGGER_RE.search(wf_desc):
            findings.append(
                ReviewFinding(
                    rule_id="CA-P1-002",
                    severity="P1",
                    resource=f"Workflow['{wf_name}'].description",
                    title="Negative trigger words in Workflow entry description",
                    detail=f"Workflow `{wf_name}` uses negative scoping in `description` (`{wf_desc[:100]}`).",
                    recommendation="Write positive customer-intent entry criteria with zero negative scoping.",
                )
            )

    # 3. Inspect CES & Dialogflow Tools
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_name = tool.get("displayName") or tool.get("shortName") or tool.get("name") or "tool"
        tool_res = f"Tool['{tool_name}']"
        tool_type = tool.get("toolType") or ""
        py_fn = tool.get("pythonFunction") or {}
        py_code = str(tool.get("pythonCode") or py_fn.get("pythonCode") or "")
        declared_fn_name = str(py_fn.get("name") or "").strip()

        # CA-P0-004: CES Python Function Parity & Docstring Schema
        if tool_type == "PYTHON_FUNCTION" or py_fn or py_code:
            match = DEF_FUNC_RE.search(py_code)
            code_fn_name = match.group(1) if match else ""
            code_args = match.group(2) if match else ""

            if not match or (declared_fn_name and declared_fn_name != code_fn_name):
                findings.append(
                    ReviewFinding(
                        rule_id="CA-P0-004",
                        severity="P0",
                        resource=tool_res,
                        title="CES Python function name mismatch between pythonFunction.name and pythonCode",
                        detail=(
                            f"`pythonFunction.name` is `{declared_fn_name}`, but `pythonCode` defines "
                            f"`def {code_fn_name or '<missing>'}(...)`."
                        ),
                        recommendation=(
                            "Ensure `pythonFunction.name` matches `def <function_name>(...)` in `pythonCode` "
                            "character-for-character."
                        ),
                    )
                )
            else:
                has_docstring = '"""' in py_code or "'''" in py_code
                has_args_returns = "Args:" in py_code and "Returns:" in py_code
                has_type_hints = (":" in code_args) or not code_args.strip()
                if not (has_docstring and has_args_returns and has_type_hints):
                    findings.append(
                        ReviewFinding(
                            rule_id="CA-P0-004",
                            severity="P1",
                            resource=tool_res,
                            title="CES Python function missing Google-style docstring or parameter type hints",
                            detail=(
                                f"`def {code_fn_name}` is missing type hints or Google-style `Args:`/`Returns:` "
                                "docstring sections required by CES for automatic JSON schema extraction."
                            ),
                            recommendation=(
                                "Add Python type hints with defaults and a Google-style docstring containing "
                                "`Args:` and `Returns:` sections."
                            ),
                        )
                    )

        # CA-P1-005: Mutation tool confirmationRequirement
        check_id = (declared_fn_name or str(tool_name)).lower()
        conf_req = str(tool.get("confirmationRequirement") or "UNSPECIFIED").upper()
        if any(check_id.startswith(prefix) for prefix in MUTATION_PREFIXES) and conf_req in (
            "NOT_REQUIRED",
            "UNSPECIFIED",
        ):
            findings.append(
                ReviewFinding(
                    rule_id="CA-P1-005",
                    severity="P1",
                    resource=tool_res,
                    title="State-modifying tool configured without human agent confirmation",
                    detail=(
                        f"Tool `{tool_name}` appears to modify state (`{check_id}`) but has "
                        f"`confirmationRequirement: {conf_req}`."
                    ),
                    recommendation=(
                        'Set `"confirmationRequirement": "REQUIRED"` in `cesToolSpecs` / workflow `cesToolSpec` '
                        "so the human agent confirms state-modifying actions in the UI before execution."
                    ),
                )
            )

    # 4. Inspect ConversationProfile (CA-P1-004: Datastore rewriter, Summarization, STT)
    if profile:
        haa_cfg = profile.get("humanAgentAssistantConfig") or {}
        sugg_cfg = haa_cfg.get("humanAgentSuggestionConfig") or {}
        feature_configs = sugg_cfg.get("featureConfigs") or []

        has_summarization = False
        for fc in feature_configs:
            if not isinstance(fc, dict):
                continue
            sf_type = ((fc.get("suggestionFeature") or {}).get("type") or "").upper()
            if sf_type in ("CONVERSATION_SUMMARIZATION", "CONVERSATION_SUMMARIZATION_VOICE"):
                has_summarization = True
            if sf_type in ("KNOWLEDGE_ASSIST", "AGENTIC_KNOWLEDGE_ASSIST", "KNOWLEDGE_SEARCH"):
                query_cfg = fc.get("queryConfig") or {}
                # Check datastore / rewriter settings
                if not query_cfg:
                    findings.append(
                        ReviewFinding(
                            rule_id="CA-P1-004",
                            severity="P1",
                            resource=f"ConversationProfile.featureConfigs[{sf_type}]",
                            title="Knowledge Assist / Datastore feature missing queryConfig & rewriter settings",
                            detail=(
                                f"`{sf_type}` is enabled on the ConversationProfile without an explicit `queryConfig` "
                                "(datastore / query rewriter settings)."
                            ),
                            recommendation=(
                                "Configure `queryConfig` (including datastore source and query rewriter settings) "
                                "and note that any triggered Guidance card suppresses Knowledge Assist on that turn."
                            ),
                        )
                    )

        if not has_summarization:
            findings.append(
                ReviewFinding(
                    rule_id="CA-P1-004",
                    severity="P1",
                    resource="ConversationProfile.humanAgentAssistantConfig",
                    title="CONVERSATION_SUMMARIZATION feature missing from ConversationProfile",
                    detail=(
                        "`CONVERSATION_SUMMARIZATION` is not enabled in `featureConfigs`. "
                        "`<agent-assist-companion-agent>` requires it for Handoff and Wrap-Up summaries."
                    ),
                    recommendation=(
                        "Add a `CONVERSATION_SUMMARIZATION` feature config (with generator/model producing "
                        "`sortedTextSections` and `answerRecord`) to `humanAgentSuggestionConfig.featureConfigs`."
                    ),
                )
            )

        stt_cfg = profile.get("sttConfig") or {}
        if stt_cfg and (not stt_cfg.get("useLongFormModel") or not stt_cfg.get("useGeminiAsr")):
            findings.append(
                ReviewFinding(
                    rule_id="CA-P1-004",
                    severity="P2",
                    resource="ConversationProfile.sttConfig",
                    title="Voice STT config not using recommended LongForm / Gemini ASR settings",
                    detail=f"Current sttConfig: {stt_cfg}",
                    recommendation=(
                        "For voice profiles, consider setting `useLongFormModel: true` and `useGeminiAsr: true` "
                        "in `sttConfig` for improved speech endpointing and recognition accuracy."
                    ),
                )
            )

    severity_rank = {"P0": 0, "P1": 1, "P2": 2}
    findings.sort(key=lambda f: (severity_rank.get(f.severity, 9), f.rule_id, f.resource))
    return findings


def format_review_markdown(bundle: dict[str, Any], findings: list[ReviewFinding]) -> str:
    """Formats review findings into a clear, prioritized Markdown report."""
    manifest = bundle.get("manifest") or {}
    agent = bundle.get("companion_agent") or {}
    profile = bundle.get("conversation_profile") or {}

    agent_name = agent.get("displayName") or agent.get("name") or manifest.get("companionAgent") or "Unknown Agent"
    profile_name = profile.get("displayName") or profile.get("name") or manifest.get("conversationProfile") or "None"

    p0_count = sum(1 for f in findings if f.severity == "P0")
    p1_count = sum(1 for f in findings if f.severity == "P1")
    p2_count = sum(1 for f in findings if f.severity == "P2")

    lines = [
        "# Companion Agent Configuration Review Report",
        "",
        f"- **Companion Agent**: `{agent_name}`",
        f"- **Conversation Profile**: `{profile_name}`",
        f"- **Workflows Audited**: {len(bundle.get('workflows') or [])}",
        f"- **Tools Audited**: {len(bundle.get('tools') or [])}",
        f"- **Summary**: **{p0_count} P0** (Critical) | **{p1_count} P1** (High) | **{p2_count} P2** (Recommended)",
        "",
    ]

    if not findings:
        lines.append("✅ **No best-practice violations detected!** All checked rules passed.")
        return "\n".join(lines) + "\n"

    for sev, label in [("P0", "Critical Blockers (P0)"), ("P1", "High-Priority Improvements (P1)"), ("P2", "Hygiene & Polish (P2)")]:
        sev_findings = [f for f in findings if f.severity == sev]
        if not sev_findings:
            continue
        lines.append(f"## {label}")
        lines.append("")
        for idx, f in enumerate(sev_findings, start=1):
            lines.append(f"### {idx}. [{f.rule_id}] {f.title}")
            lines.append(f"- **Resource**: `{f.resource}`")
            lines.append(f"- **Finding**: {f.detail}")
            lines.append(f"- **Recommendation**: {f.recommendation}")
            lines.append("")

    return "\n".join(lines)


def format_review_json(bundle: dict[str, Any], findings: list[ReviewFinding]) -> dict[str, Any]:
    """Formats review findings into a structured JSON dictionary."""
    return {
        "summary": {
            "total": len(findings),
            "P0": sum(1 for f in findings if f.severity == "P0"),
            "P1": sum(1 for f in findings if f.severity == "P1"),
            "P2": sum(1 for f in findings if f.severity == "P2"),
        },
        "findings": [asdict(f) for f in findings],
    }
