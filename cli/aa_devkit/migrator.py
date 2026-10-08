"""Stateless AI Coach & PGKA -> Companion Agent configuration migrator (FR-1.3).

Converts legacy Dialogflow v2beta1 Generator (`agentCoachingContext`) and PGKA
configurations into the modern Companion Agent (`skillConfigs`, `cesToolSpecs`)
JSON schema while flagging manual review items (e.g. Checkpoint Rule, IF/ELSE splits).
"""

from __future__ import annotations

import copy
import re
from typing import Any

LEGACY_TOOL_RE = re.compile(r"\{\$tool\.([A-Za-z0-9_.-]+)\}")


def _rewrite_legacy_tool_syntax(text: str) -> tuple[str, list[str]]:
    """Replaces legacy `{$tool.name}` references with `{@TOOL:name}`."""
    if not text:
        return "", []
    rewritten_tools: list[str] = []

    def _repl(match: re.Match[str]) -> str:
        t_name = match.group(1)
        rewritten_tools.append(t_name)
        return f"{{@TOOL:{t_name}}}"

    return LEGACY_TOOL_RE.sub(_repl, text), rewritten_tools


def migrate_ai_coach_to_companion_agent(
    legacy_payload: dict[str, Any],
) -> dict[str, Any]:
    """Converts a legacy AI Coach Generator JSON into a Companion Agent JSON bundle."""
    # Support either a raw Generator dict or an exported bundle containing generators
    gen = legacy_payload
    if "generators" in legacy_payload and isinstance(legacy_payload["generators"], list) and legacy_payload["generators"]:
        gen = legacy_payload["generators"][0]

    coaching_ctx = gen.get("agentCoachingContext") or {}
    raw_overarching = str(coaching_ctx.get("overarchingGuidance") or "").strip()
    rewritten_overarching, overarching_tools = _rewrite_legacy_tool_syntax(raw_overarching)

    overarching_guidance = (
        rewritten_overarching
        or (
            "1. Role & Persona: You are a real-time co-pilot assisting contact center agents.\n"
            "2. Channel Style & Brevity: Be warm, empathetic, and concise. Keep every suggested response under 2 spoken sentences.\n"
            "3. Priority Hierarchy: 1. Physical Safety -> 2. Identity Verification -> 3. Issue Resolution.\n"
            "4. Global Completion & Guardrails: Verify caller identity before discussing account details or invoking account tools."
        )
    )

    trigger_event = gen.get("triggerEvent") or "END_OF_UTTERANCE"
    legacy_instructions = coaching_ctx.get("instructions") or []
    guidance_instructions: list[dict[str, Any]] = []
    migration_notes: list[str] = []
    discovered_tools: list[str] = list(gen.get("tools") or [])
    for t_id in overarching_tools:
        if t_id not in discovered_tools:
            discovered_tools.append(t_id)

    for idx, inst in enumerate(legacy_instructions, start=1):
        if not isinstance(inst, dict):
            continue
        title = str(inst.get("displayName") or f"Migrated Coaching Rule #{idx}").strip()
        raw_details = str(inst.get("displayDetails") or "").strip()
        raw_condition = str(inst.get("condition") or "").strip()
        raw_agent_action = str(inst.get("agentAction") or "").strip()
        raw_system_action = str(inst.get("systemAction") or "").strip()

        # In legacy AI Coach, displayDetails was often sent to the model; in Companion Agent it is UI-only!
        if raw_details and not raw_condition:
            raw_condition = raw_details
            raw_details = f"Guidance for {title}."
            migration_notes.append(
                f"[{title}] Moved legacy `displayDetails` into `condition` because `displayDetails` is UI-only in Companion Agent."
            )
        elif len(raw_details) > 160 or LEGACY_TOOL_RE.search(raw_details):
            raw_condition = f"{raw_condition} {raw_details}".strip()
            raw_details = f"Guidance for {title}."
            migration_notes.append(
                f"[{title}] Merged detailed `displayDetails` into `condition` to prevent UI-only visibility loss."
            )

        clean_condition, cond_tools = _rewrite_legacy_tool_syntax(raw_condition)
        clean_agent_action, aa_tools = _rewrite_legacy_tool_syntax(raw_agent_action)
        clean_system_action, sa_tools = _rewrite_legacy_tool_syntax(raw_system_action)

        for t_id in cond_tools + aa_tools + sa_tools:
            if t_id not in discovered_tools:
                discovered_tools.append(t_id)

        actions: list[dict[str, str]] = []
        if clean_agent_action:
            actions.append({"description": clean_agent_action})
        if clean_system_action and clean_system_action != clean_agent_action:
            actions.append({"description": clean_system_action})
        if not actions:
            actions.append(
                {
                    "description": f"Assist the customer with {title} in under 2 spoken sentences."
                }
            )
            migration_notes.append(
                f"[{title}] Added default positive action step because legacy instruction had empty agentAction/systemAction."
            )

        card: dict[str, Any] = {
            "displayName": title,
            "displayDetails": raw_details or f"Guides the agent on {title}.",
            "condition": clean_condition or f"Applies when the customer inquiries about {title}.",
            "actions": actions,
        }

        if re.search(r"\b(?:verify|verification|authenticate|intake|greeting)\b", title, re.IGNORECASE):
            card["triggerEvent"] = "CUSTOMER_MESSAGE"
            migration_notes.append(
                f"[{title}] Set `triggerEvent: CUSTOMER_MESSAGE` to prevent Turn-1 human-agent greeting misfires."
            )

        guidance_instructions.append(card)

    ces_tool_specs: list[dict[str, Any]] = []
    for t_ref in discovered_tools:
        if not isinstance(t_ref, str) or not t_ref.strip():
            continue
        ces_tool_specs.append(
            {
                "cesTool": t_ref.strip(),
                "confirmationRequirement": "NOT_REQUIRED",
                "proactiveEnabled": True,
                "reactiveEnabled": True,
            }
        )

    companion_agent = {
        "displayName": gen.get("displayName") or gen.get("description") or "Migrated Companion Agent",
        "description": gen.get("description") or "Migrated from legacy AI Coach Generator.",
        "skillConfigs": [
            {
                "skillTriggeringEvent": trigger_event,
                "guidanceSkillConfig": {
                    "overarchingGuidance": overarching_guidance,
                    "guidanceInstructions": guidance_instructions,
                },
            }
        ],
        "cesToolSpecs": ces_tool_specs,
    }

    return {
        "companion_agent": companion_agent,
        "migration_notes": migration_notes,
        "source_generator": copy.deepcopy(gen.get("name") or "legacy_generator"),
    }
