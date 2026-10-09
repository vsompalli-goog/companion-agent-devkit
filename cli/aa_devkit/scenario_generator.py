"""Config-Aware Synthetic Scenario Generator & 5D Persona / 4-Strategy Perturbation Harness.

Implements two core simulator capabilities:
  1. Config-Aware Synthetic Scenario Generator (`aa-devkit generate-scenarios`):
     - Reads an exported CompanionAgent bundle (`companion_agent.json`, `workflows/`, `tools/`).
     - Enforces the Multi-Step Workflow Pacing Rule (`1 workflow step = 2 turns`, auto-expanding
       `turn_count` to `max(turn_count, min(36, max_wf_steps * 2 + 2))`).
     - Interleaves Negative-Suppression Small-Talk / Greeting turns (`must_suppress: true`).
     - Populates ground-truth `expected_guidance_cards`, `expected_tools`, and `expected_entities`
       derived directly from the agent's `guidanceInstructions` and tool `inputSchema` definitions.
  2. 5D Dynamic Customer Persona & 4-Strategy Agent Perturbation Harness (`aa-devkit simulate` / FR-5.5):
     - 5D Customer Persona (`tone`, `tech_literacy`, `patience`, `escalation_tendency`, `verbosity`)
       with strict slot drip-feeding, dynamic patience decay on repetitive/stalling agent turns,
       and automatic tone escalation (`polite` -> `frustrated` -> `adversarial`).
     - 4-Strategy Virtual Human Agent (`strict`, `paraphrase`, `deviate`, `probe_negative`) to
       stress-test Companion Agent recovery and negative suppression.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from typing import Any, Optional

from aa_devkit.client import CompanionAgentRestClient

TONE_GUIDANCE = {
    "polite": "Warm, cooperative, and courteous. Expresses appreciation when helped.",
    "confused": "Uncertain about terminology, asks clarifying questions, hesitates before giving details.",
    "frustrated": "Visibly annoyed by delays or friction, uses sharper phrasing, expects quick resolution.",
    "rushed": "In a hurry, gives brief answers, asks how long steps will take.",
    "adversarial": "Skeptical, challenging, demands supervisor escalation if stalled.",
}

TECH_LITERACY_GUIDANCE = {
    "low": (
        "Uses everyday non-technical language, struggles with exact IDs/portal navigation, "
        "and provides AT MOST 1 piece of information per turn."
    ),
    "medium": (
        "Comfortable with basic account/order details, provides 1-2 pieces of information "
        "when explicitly asked."
    ),
    "high": (
        "Precise and articulate with technical terms and error codes, provides up to 2 "
        "pieces of requested information clearly."
    ),
}

VERBOSITY_GUIDANCE = {
    "concise": "Short 1-sentence replies (8-18 words).",
    "normal": "Natural conversational replies (15-35 words).",
    "verbose": "Detailed replies with extra context or backstory (30-55 words).",
}

AGENT_STRATEGIES = ("strict", "paraphrase", "deviate", "probe_negative")


@dataclass
class Persona5DState:
    """Tracks the 5-Dimensional Dynamic Customer Persona state across turns (FR-5.5)."""

    initial_tone: str = "polite"
    current_tone: str = "polite"
    tech_literacy: str = "medium"
    initial_patience: int = 4
    current_patience: int = 4
    escalation_tendency: str = "medium"
    verbosity: str = "normal"
    turns_elapsed: int = 0
    stalled_turns: int = 0
    escalation_requested: bool = False
    disclosed_slots: list[str] = field(default_factory=list)
    tone_history: list[str] = field(default_factory=list)


def init_5d_persona_state(
    tone: str = "polite",
    tech_literacy: str = "medium",
    patience: int = 4,
    escalation_tendency: str = "medium",
    verbosity: str = "normal",
) -> Persona5DState:
    """Initializes a 5D Customer Persona state."""
    clean_tone = tone.lower() if tone.lower() in TONE_GUIDANCE else "polite"
    clean_tech = tech_literacy.lower() if tech_literacy.lower() in TECH_LITERACY_GUIDANCE else "medium"
    clean_verb = verbosity.lower() if verbosity.lower() in VERBOSITY_GUIDANCE else "normal"
    clamped_patience = max(1, min(5, int(patience)))
    return Persona5DState(
        initial_tone=clean_tone,
        current_tone=clean_tone,
        tech_literacy=clean_tech,
        initial_patience=clamped_patience,
        current_patience=clamped_patience,
        escalation_tendency=escalation_tendency.lower(),
        verbosity=clean_verb,
        tone_history=[clean_tone],
    )


def update_5d_persona_state(
    state: Persona5DState,
    last_agent_utterance: str,
    previous_agent_utterance: str = "",
) -> Persona5DState:
    """Updates caller patience and tone before generating the next caller turn.

    Decays `current_patience` when the human agent repeats themselves or uses stalling
    phrases without progressing the workflow, shifting tone (`polite` -> `frustrated` -> `adversarial`)
    and triggering a supervisor escalation request when patience hits 1.
    """
    state.turns_elapsed += 1
    agent_clean = (last_agent_utterance or "").strip().lower()
    prev_clean = (previous_agent_utterance or "").strip().lower()

    is_repetitive = bool(agent_clean and prev_clean and agent_clean == prev_clean)
    is_stall = any(
        phrase in agent_clean
        for phrase in (
            "still checking",
            "one more moment",
            "please continue to hold",
            "system is slow",
            "i don't have an update yet",
            "let me look into that again",
        )
    )

    if is_repetitive or is_stall:
        state.stalled_turns += 1
        state.current_patience = max(1, state.current_patience - 1)
    elif state.turns_elapsed > 6 and state.turns_elapsed % 4 == 0:
        state.current_patience = max(1, state.current_patience - 1)

    if state.current_patience <= 1:
        state.current_tone = "adversarial"
        if state.escalation_tendency in ("medium", "high"):
            state.escalation_requested = True
    elif state.current_patience == 2 and state.current_tone in ("polite", "confused"):
        state.current_tone = "frustrated"

    state.tone_history.append(state.current_tone)
    return state


def build_5d_persona_directive(state: Persona5DState, remaining_slots: dict[str, Any]) -> str:
    """Builds the 5D Persona & Slot Drip-Feeding prompt directive for a caller turn."""
    max_slots_this_turn = 1 if state.tech_literacy == "low" else 2
    allowed_keys = list(remaining_slots.keys())[:max_slots_this_turn]
    allowed_subset = {k: remaining_slots[k] for k in allowed_keys}

    escalation_note = (
        "CRITICAL: Your patience is exhausted (1/5). Demand to speak with a supervisor or manager immediately."
        if state.escalation_requested
        else "Do not ask for a supervisor unless the agent refuses to help."
    )

    return (
        f"5D CALLER PERSONA DIRECTIVE:\n"
        f"- Tone ({state.current_tone}): {TONE_GUIDANCE.get(state.current_tone, '')}\n"
        f"- Tech Literacy ({state.tech_literacy}): {TECH_LITERACY_GUIDANCE.get(state.tech_literacy, '')}\n"
        f"- Patience Level: {state.current_patience}/5 (Stalled turns: {state.stalled_turns})\n"
        f"- Verbosity ({state.verbosity}): {VERBOSITY_GUIDANCE.get(state.verbosity, '')}\n"
        f"- STRICT SLOT DRIP-FEEDING: Reveal AT MOST {max_slots_this_turn} requested detail(s) in this turn: "
        f"{json.dumps(allowed_subset)}. Never volunteer unrequested parameters.\n"
        f"- Escalation Rule: {escalation_note}"
    )


def apply_agent_strategy(
    base_agent_utterance: str,
    strategy: str = "strict",
    guidance_card_title: str = "",
    requested_slot: str = "",
) -> dict[str, Any]:
    """Applies one of the 4 Virtual Human Agent perturbation strategies (FR-5.5).

    Strategies:
      - `strict`: Follows the Companion Agent's guidance card closely.
      - `paraphrase`: Rephrases the guidance naturally while preserving slot-filling intent.
      - `deviate`: Deliberately deviates from the guidance card to test Companion Agent recovery.
      - `probe_negative`: Emits a low-signal filler/hold turn (`must_suppress: true`) to test suppression.
    """
    strat = (strategy or "strict").strip().lower()
    if strat not in AGENT_STRATEGIES:
        strat = "strict"

    if strat == "strict":
        return {
            "strategy": "strict",
            "text": base_agent_utterance,
            "expects_recovery_on_next_turn": False,
            "injected_negative_probe": False,
        }

    if strat == "paraphrase":
        paraphrased = base_agent_utterance
        if requested_slot:
            slot_label = requested_slot.replace("_", " ")
            paraphrased = (
                f"I'd be happy to help you with that right away. So I can pull up the right record, "
                f"could you share your {slot_label} with me?"
            )
        elif not paraphrased.lower().startswith("absolutely"):
            paraphrased = f"Absolutely, let's take care of that together. {base_agent_utterance}"
        return {
            "strategy": "paraphrase",
            "text": paraphrased,
            "expects_recovery_on_next_turn": False,
            "injected_negative_probe": False,
        }

    if strat == "deviate":
        deviated = (
            "Before we look at that, have you heard about our annual rewards newsletter? "
            "Also, our system is running a bit slow today, please bear with me."
        )
        return {
            "strategy": "deviate",
            "text": deviated,
            "expects_recovery_on_next_turn": True,
            "missed_guidance_card": guidance_card_title,
            "injected_negative_probe": False,
        }

    # probe_negative
    return {
        "strategy": "probe_negative",
        "text": "One moment please.",
        "expects_recovery_on_next_turn": False,
        "injected_negative_probe": True,
    }


def _extract_bundle_capabilities(bundle: dict[str, Any]) -> dict[str, Any]:
    """Extracts guidance cards, workflow step counts, and tool parameter schemas from a bundle."""
    agent = bundle.get("companion_agent") or {}
    cards: list[dict[str, Any]] = []

    raw_instructions = (
        agent.get("guidanceInstructions")
        or (agent.get("agentConfig") or {}).get("guidanceInstructions")
        or []
    )
    for idx, instr in enumerate(raw_instructions, start=1):
        if not isinstance(instr, dict):
            continue
        title = (
            instr.get("displayName")
            or instr.get("title")
            or instr.get("name")
            or f"Guidance Card {idx}"
        )
        text = (
            instr.get("instruction")
            or instr.get("prompt")
            or instr.get("description")
            or ""
        )
        cards.append({"title": title, "instruction": text})

    workflows = bundle.get("workflows") or []
    max_wf_steps = 0
    workflow_summaries: list[dict[str, Any]] = []
    for wf in workflows:
        if not isinstance(wf, dict):
            continue
        wf_name = wf.get("displayName") or (wf.get("name") or "workflow").split("/")[-1]
        steps = wf.get("steps") or wf.get("workflowSteps") or []
        step_count = len(steps) if isinstance(steps, list) else 3
        # Also count numbered steps in instruction markdown if `steps` list is empty
        if step_count == 0:
            instr_txt = str(wf.get("instruction") or wf.get("description") or "")
            numbered = re.findall(r"(?:^|\n)\s*\d+\.\s+", instr_txt)
            step_count = max(2, len(numbered))
        max_wf_steps = max(max_wf_steps, step_count)
        workflow_summaries.append({"name": wf_name, "step_count": step_count})

    tools_raw = bundle.get("tools") or []
    tools_info: list[dict[str, Any]] = []
    for t in tools_raw:
        if not isinstance(t, dict):
            continue
        t_name = (
            t.get("displayName")
            or t.get("action")
            or (t.get("name") or "").split("/")[-1]
        )
        if not t_name:
            continue
        schema = (
            t.get("inputSchema")
            or (t.get("functionDeclaration") or {}).get("parameters")
            or {}
        )
        props = schema.get("properties") or {}
        req_params = schema.get("required") or list(props.keys())[:2]
        sample_entities: dict[str, Any] = {}
        for p_name in req_params:
            p_low = p_name.lower()
            if "order" in p_low:
                sample_entities[p_name] = "ORD-849201"
            elif "email" in p_low:
                sample_entities[p_name] = "alex.rivera@example.com"
            elif "phone" in p_low:
                sample_entities[p_name] = "415-555-0192"
            elif "account" in p_low or "customer" in p_low or "user" in p_low:
                sample_entities[p_name] = "ACCT-392041"
            elif "amount" in p_low or "price" in p_low:
                sample_entities[p_name] = 49.99
            elif "reason" in p_low:
                sample_entities[p_name] = "damaged_item"
            else:
                sample_entities[p_name] = f"sample_{p_name}"

        tools_info.append(
            {
                "name": t_name,
                "required_params": req_params,
                "sample_entities": sample_entities,
                "confirmation_required": str(t.get("confirmationRequirement") or "").upper() == "REQUIRED",
            }
        )

    return {
        "cards": cards,
        "workflows": workflow_summaries,
        "max_workflow_steps": max_wf_steps,
        "tools": tools_info,
    }


def compute_workflow_paced_turn_count(requested_turns: int, max_workflow_steps: int) -> int:
    """Enforces the Multi-Step Workflow Pacing Rule: 1 workflow step = 2 turns (+2 opening/closing turns)."""
    if max_workflow_steps <= 0:
        return max(4, requested_turns)
    min_wf_turns = min(36, max_workflow_steps * 2 + 2)
    return max(requested_turns, min_wf_turns)


def generate_scenarios_from_bundle(
    bundle: dict[str, Any],
    count: int = 2,
    requested_turns: int = 6,
    include_small_talk: bool = True,
    persona_tone: str = "polite",
    persona_tech_literacy: str = "medium",
    agent_strategy: str = "strict",
    client: Optional[CompanionAgentRestClient] = None,
    use_vertex: bool = False,
) -> dict[str, Any]:
    """Generates config-aware synthetic evaluation scenarios from an exported CompanionAgent bundle.

    Enforces:
      - Multi-Step Workflow Pacing Rule (`1 workflow step = 2 turns`).
      - Rule 1B Negative Suppression turns (`must_suppress: true`) on greetings & small talk.
      - Ground-truth `expected_guidance_cards`, `expected_tools`, and `expected_entities` aligned
        with the bundle's configured guidance cards and tools.
    """
    caps = _extract_bundle_capabilities(bundle)
    cards = caps["cards"] or [
        {"title": "Verify Identity & Order Lookup", "instruction": "Ask for order ID and verify identity."},
        {"title": "Process Resolution & Confirmation", "instruction": "Confirm resolution with customer."},
    ]
    tools = caps["tools"] or [
        {
            "name": "lookup_order",
            "required_params": ["order_id"],
            "sample_entities": {"order_id": "ORD-849201"},
            "confirmation_required": False,
        }
    ]
    max_wf_steps = caps["max_workflow_steps"] or len(cards)
    effective_turns = compute_workflow_paced_turn_count(requested_turns, max_wf_steps)

    conversations: list[dict[str, Any]] = []

    for sc_idx in range(1, max(1, count) + 1):
        persona = init_5d_persona_state(
            tone=persona_tone,
            tech_literacy=persona_tech_literacy,
        )
        turns: list[dict[str, Any]] = []
        turn_id = 1

        # Turn 1: Opening Greeting (Negative Suppression / Rule 1B)
        if include_small_talk:
            turns.append(
                {
                    "turn_id": turn_id,
                    "role": "END_USER",
                    "text": "Hi there, good morning!",
                    "expected": {
                        "must_suppress": True,
                        "expected_guidance_cards": [],
                        "expected_tools": [],
                        "expected_entities": {},
                        "not_expected_entities": {},
                    },
                }
            )
            turn_id += 1
            turns.append(
                {
                    "turn_id": turn_id,
                    "role": "HUMAN_AGENT",
                    "text": "Good morning! Thank you for contacting support. How can I help you today?",
                    "expected": {
                        "must_suppress": True,
                        "expected_guidance_cards": [],
                        "expected_tools": [],
                        "expected_entities": {},
                        "not_expected_entities": {},
                    },
                }
            )
            turn_id += 1

        # Interleave workflow / guidance card steps (1 step = 2 turns: END_USER intent -> HUMAN_AGENT action)
        first_card = cards[0]["title"]
        first_tool = tools[0]
        all_slots = dict(first_tool["sample_entities"])
        slot_items = list(all_slots.items())
        primary_slot_k, primary_slot_v = slot_items[0] if slot_items else ("order_id", "ORD-849201")

        # Step 1: Caller states problem WITHOUT volunteering all parameters upfront (slot drip-feeding)
        turns.append(
            {
                "turn_id": turn_id,
                "role": "END_USER",
                "text": (
                    f"I'm calling because I have an issue with my recent order and need help resolving it. "
                    f"({persona.current_tone} tone)"
                ),
                "expected": {
                    "must_suppress": False,
                    "expected_guidance_cards": [first_card],
                    "expected_tools": [],
                    "expected_entities": {},
                    "not_expected_entities": {primary_slot_k: ""},
                },
            }
        )
        turn_id += 1

        # Step 1 Agent response (perturbed by `agent_strategy`)
        base_agent_step1 = (
            f"I can certainly help you with that. Could you please provide your "
            f"{primary_slot_k.replace('_', ' ')} so I can look that up?"
        )
        strat_res = apply_agent_strategy(
            base_agent_utterance=base_agent_step1,
            strategy=agent_strategy if sc_idx > 1 else "strict",
            guidance_card_title=first_card,
            requested_slot=primary_slot_k,
        )
        update_5d_persona_state(persona, strat_res["text"])
        turns.append(
            {
                "turn_id": turn_id,
                "role": "HUMAN_AGENT",
                "text": strat_res["text"],
                "expected": {
                    "must_suppress": bool(strat_res.get("injected_negative_probe", False)),
                    "expected_guidance_cards": [],
                    "expected_tools": [],
                    "expected_entities": {},
                    "not_expected_entities": {},
                },
            }
        )
        turn_id += 1

        # Step 2: Caller drip-feeds the requested slot -> triggers tool call + second guidance card
        second_card = cards[min(1, len(cards) - 1)]["title"]
        turns.append(
            {
                "turn_id": turn_id,
                "role": "END_USER",
                "text": f"Sure, my {primary_slot_k.replace('_', ' ')} is {primary_slot_v}.",
                "expected": {
                    "must_suppress": False,
                    "expected_guidance_cards": [second_card],
                    "expected_tools": [first_tool["name"]],
                    "expected_entities": {primary_slot_k: primary_slot_v},
                    "not_expected_entities": {},
                },
            }
        )
        turn_id += 1

        turns.append(
            {
                "turn_id": turn_id,
                "role": "HUMAN_AGENT",
                "text": (
                    f"Thank you! I've located `{primary_slot_v}` using `{first_tool['name']}` "
                    f"and completed the next step in the workflow for you."
                ),
                "expected": {
                    "must_suppress": True,
                    "expected_guidance_cards": [],
                    "expected_tools": [],
                    "expected_entities": {},
                    "not_expected_entities": {},
                },
            }
        )
        turn_id += 1

        # Add remaining workflow steps (1 step = 2 turns) until `effective_turns` is reached
        step_cursor = 2
        while len(turns) < effective_turns:
            if include_small_talk and len(turns) == effective_turns - 2:
                # Closing small-talk / thank-you turn (Rule 1B negative suppression)
                turns.append(
                    {
                        "turn_id": turn_id,
                        "role": "END_USER",
                        "text": "Thanks, that's all I needed!",
                        "expected": {
                            "must_suppress": True,
                            "expected_guidance_cards": [],
                            "expected_tools": [],
                            "expected_entities": {},
                            "not_expected_entities": {},
                        },
                    }
                )
                turn_id += 1
                if len(turns) < effective_turns:
                    turns.append(
                        {
                            "turn_id": turn_id,
                            "role": "HUMAN_AGENT",
                            "text": "You're very welcome! Have a wonderful rest of your day.",
                            "expected": {
                                "must_suppress": True,
                                "expected_guidance_cards": [],
                                "expected_tools": [],
                                "expected_entities": {},
                                "not_expected_entities": {},
                            },
                        }
                    )
                    turn_id += 1
                break

            card_for_step = cards[min(step_cursor, len(cards) - 1)]["title"]
            tool_for_step = tools[min(step_cursor, len(tools) - 1)]
            step_entities = dict(tool_for_step["sample_entities"])

            turns.append(
                {
                    "turn_id": turn_id,
                    "role": "END_USER",
                    "text": (
                        f"Can we proceed with step {step_cursor + 1} now? "
                        f"Please confirm everything is set for {', '.join(f'{k}={v}' for k, v in step_entities.items())}."
                    ),
                    "expected": {
                        "must_suppress": False,
                        "expected_guidance_cards": [card_for_step],
                        "expected_tools": [tool_for_step["name"]],
                        "expected_entities": step_entities,
                        "not_expected_entities": {},
                    },
                }
            )
            turn_id += 1

            if len(turns) < effective_turns:
                turns.append(
                    {
                        "turn_id": turn_id,
                        "role": "HUMAN_AGENT",
                        "text": f"I have confirmed step {step_cursor + 1} and applied `{ card_for_step }`.",
                        "expected": {
                            "must_suppress": True,
                            "expected_guidance_cards": [],
                            "expected_tools": [],
                            "expected_entities": {},
                            "not_expected_entities": {},
                        },
                    }
                )
                turn_id += 1
            step_cursor += 1

        conversations.append(
            {
                "conversation_id": f"synth-scenario-{sc_idx:03d}",
                "description": (
                    f"Config-aware synthetic scenario #{sc_idx} "
                    f"(workflow_steps={max_wf_steps}, paced_turns={len(turns)}, "
                    f"persona_tone={persona.current_tone}, agent_strategy={agent_strategy})"
                ),
                "persona_5d": asdict(persona),
                "agent_strategy": agent_strategy,
                "ingested_context": {"crm_tier": "Gold", "channel": "voice"},
                "turns": turns,
            }
        )

    return {
        "schemaVersion": "1.0",
        "generatorMetadata": {
            "requestedTurns": requested_turns,
            "effectivePacedTurns": effective_turns,
            "maxWorkflowSteps": max_wf_steps,
            "workflowPacingRule": "1 workflow step = 2 turns (+2 opening/closing turns)",
            "negativeSuppressionTurnsIncluded": include_small_talk,
            "configuredGuidanceCards": [c["title"] for c in cards],
            "configuredTools": [t["name"] for t in tools],
            "usedVertexAi": bool(use_vertex and client is not None),
        },
        "conversations": conversations,
    }
