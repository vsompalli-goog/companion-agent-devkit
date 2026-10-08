"""Unit tests for FR-1.3, FR-1.5, and Sections 7.5 – 7.8 (L3 & L4)."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
import pytest

from aa_devkit.eval_pipeline import evaluate_dataset, score_single_turn
from aa_devkit.hillclimb import (
    analyze_loss_patterns,
    estimate_eval_cost,
    propose_bundle_improvements,
)
from aa_devkit.latency import (
    LATENCY_TAXONOMY,
    analyze_latency_dataset,
    format_latency_html_dashboard,
    format_latency_markdown,
)
from aa_devkit.migrator import migrate_ai_coach_to_companion_agent
from aa_devkit.skill_eval import run_skill_regression_eval
from aa_devkit.voice_tester import (
    StreamingProfile,
    chunk_audio_bytes,
    replay_voice_manifest,
    synthesize_transcript_audio,
    validate_stock_voice,
)


def test_migrator_ai_coach_to_companion_agent() -> None:
    """FR-1.3: Legacy AI Coach Generator converts cleanly to Companion Agent JSON."""
    legacy_generator = {
        "name": "projects/p1/locations/us-central1/generators/gen-1",
        "displayName": "Legacy Billing AI Coach",
        "triggerEvent": "END_OF_UTTERANCE",
        "agentCoachingContext": {
            "overarchingGuidance": "Help agents with billing using {$tool.lookup_invoice}.",
            "instructions": [
                {
                    "displayName": "Caller Identity Verification",
                    "displayDetails": "When caller is unverified, run {$tool.verify_pin} first.",
                    "condition": "",
                    "agentAction": "Ask caller for their 4-digit PIN and call {$tool.verify_pin}.",
                    "systemAction": "",
                }
            ],
        },
    }
    migrated = migrate_ai_coach_to_companion_agent(legacy_generator)
    ca = migrated["companion_agent"]
    assert ca["displayName"] == "Legacy Billing AI Coach"

    gsc = ca["skillConfigs"][0]["guidanceSkillConfig"]
    assert "{@TOOL:lookup_invoice}" in gsc["overarchingGuidance"]
    assert "{$tool." not in gsc["overarchingGuidance"]

    card = gsc["guidanceInstructions"][0]
    assert card["triggerEvent"] == "CUSTOMER_MESSAGE"
    assert "{@TOOL:verify_pin}" in card["condition"]
    assert "{@TOOL:verify_pin}" in card["actions"][0]["description"]

    tool_refs = [s["cesTool"] for s in ca["cesToolSpecs"]]
    assert "lookup_invoice" in tool_refs
    assert "verify_pin" in tool_refs
    assert len(migrated["migration_notes"]) >= 2


def test_skill_eval_golden_questions_pass() -> None:
    """FR-1.5: All golden engineering questions pass against pack/ skills and references."""
    report = run_skill_regression_eval()
    assert report["total"] >= 13
    assert report["failed"] == 0, f"Skill regression failures: {report['results']}"
    assert report["pass_rate_pct"] == 100.0


def test_eval_pipeline_scoring_and_dataset() -> None:
    """Section 7.5 (FR-4.1, FR-4.2): Turn-by-turn evaluation scoring and sample dataset."""
    sample_path = Path(__file__).resolve().parents[1] / "evals" / "samples" / "sample_eval_conversations.json"
    report = evaluate_dataset(sample_path)
    assert report["summary"]["total_turns"] == 2
    assert report["summary"]["passed_turns"] == 2
    assert report["summary"]["turn_pass_rate_pct"] == 100.0
    assert report["summary"]["negative_suppression_rate_pct"] == 100.0

    # Verify negative suppression failure detection
    bad_turn = {"turn_id": 1, "role": "HUMAN_AGENT", "text": "Hi!", "expected": {"must_suppress": True}}
    bad_resp = {
        "humanAgentSuggestionResults": [
            {
                "generateCompanionSuggestionsResponse": {
                    "companionSuggestion": {
                        "guidances": [{"instructionSource": {"displayName": "Unwanted Card"}}]
                    }
                }
            }
        ]
    }
    verdict = score_single_turn(bad_turn, bad_resp)
    assert verdict["passed"] is False
    assert any("Expected negative suppression" in f for f in verdict["failures"])


def test_voice_tester_stock_voices_and_framing(tmp_path: Path) -> None:
    """Section 7.6 (FR-5.1, FR-5.2, FR-5.3): Stock voice enforcement, PCM chunking, and replay."""
    with pytest.raises(ValueError, match="not in the approved stock voice catalog"):
        validate_stock_voice("custom-cloned-voice-v1")

    assert validate_stock_voice("en-US-Journey-F") == "en-US-Journey-F"

    # 24kHz * 2 bytes/sample * 100ms = 4800 bytes per chunk
    pcm_1sec = b"\x00\x01" * 24000
    chunks = chunk_audio_bytes(pcm_1sec, sample_rate_hz=24000, bytes_per_sample=2, chunk_ms=100)
    assert len(chunks) == 10
    assert len(chunks[0]) == 4800

    class FakeVoiceClient:
        project_id = "cust-proj-1"
        location = "global"

        def request(self, method: str, path_or_url: str, json_body: Any = None, **kwargs: Any) -> dict[str, Any]:
            if "text:synthesize" in path_or_url:
                return {"audioContent": base64.b64encode(b"\x00\x00" * 2400).decode("ascii")}
            if path_or_url.endswith("/conversations"):
                return {"name": "projects/cust-proj-1/locations/global/conversations/c1"}
            if path_or_url.endswith("/participants"):
                role = (json_body or {}).get("role", "END_USER")
                return {"name": f"projects/cust-proj-1/locations/global/conversations/c1/participants/{role}"}
            return {"message": {"content": "recognized speech"}}

    turns = [
        {"turn_id": 1, "role": "END_USER", "text": "Hello I need help with my billing statement."},
        {"turn_id": 2, "role": "HUMAN_AGENT", "text": "Happy to help with your statement."},
    ]
    manifest = synthesize_transcript_audio(
        client=FakeVoiceClient(),  # type: ignore[arg-type]
        turns=turns,
        output_dir=tmp_path / "synth_out",
        streaming_profile=StreamingProfile(chunk_ms=100, cadence_ms=10),
    )
    assert manifest["derivedFromCustomerData"] is True
    assert manifest["voiceCloningUsed"] is False
    assert len(manifest["turns"]) == 2

    replay_res = replay_voice_manifest(
        client=FakeVoiceClient(),  # type: ignore[arg-type]
        manifest_dir=tmp_path / "synth_out",
        profile_name="projects/cust-proj-1/locations/global/conversationProfiles/cp1",
    )
    assert replay_res["turns_replayed"] == 2
    assert replay_res["derivedFromCustomerData"] is True


def test_latency_breakdown_and_observability() -> None:
    """Section 7.7 (FR-6.1 – FR-6.4): 6-stage taxonomy, API metric merge, and reports."""
    assert len(LATENCY_TAXONOMY) == 6
    sample_path = Path(__file__).resolve().parents[1] / "evals" / "samples" / "sample_eval_conversations.json"
    import json

    dataset = json.loads(sample_path.read_text(encoding="utf-8"))
    # Inject an API observability metric override to verify FR-6.3 merge
    dataset["conversations"][0]["turns"][1]["actual_response"]["observabilityMetrics"] = {
        "llm_generation_vertex": 590.0,
        "quota_throttling": 15.0,
    }
    report = analyze_latency_dataset(dataset)
    assert report["aggregate"]["spans"]["llm_generation_vertex"]["max"] == 590
    assert report["aggregate"]["spans"]["quota_throttling"]["max"] == 15

    md = format_latency_markdown(report)
    assert "customer_integration_network" in md
    assert "quota_throttling" in md

    html_out = format_latency_html_dashboard(report)
    assert "<title>Companion Agent Latency Breakdown Dashboard</title>" in html_out


def test_hillclimb_cost_estimation_and_proposals() -> None:
    """Section 7.8 (FR-7.1 – FR-7.3): Pre-run cost estimator and loss-pattern proposals."""
    bundle = {
        "companion_agent": {
            "displayName": "Test Agent",
            "skillConfigs": [
                {
                    "skillTriggeringEvent": "END_OF_UTTERANCE",
                    "guidanceSkillConfig": {
                        "guidanceInstructions": [
                            {
                                "displayName": "Caller Verification Card",
                                "condition": "When caller asks for account info.",
                                "actions": [{"description": "Verify caller identity."}],
                            },
                            {
                                "displayName": "Lookup_Order Status Card",
                                "condition": "When caller asks about order status.",
                                "actions": [{"description": "Check the order status."}],
                            },
                        ]
                    },
                }
            ],
        },
        "tools": [],
    }
    dataset = {
        "conversations": [
            {
                "conversation_id": "c1",
                "turns": [
                    {"turn_id": 1, "role": "HUMAN_AGENT", "text": "Hello!"},
                    {"turn_id": 2, "role": "END_USER", "text": "Where is my order?"},
                ],
            }
        ]
    }
    cost = estimate_eval_cost(dataset, bundle, iterations=2)
    assert cost["total_analyze_content_calls"] == 4
    assert cost["estimated_input_tokens"] > 0
    assert cost["estimated_cost_usd"] >= 0.0

    fake_eval_report = {
        "summary": {
            "total_conversations": 1,
            "total_turns": 2,
            "passed_turns": 0,
            "failed_turns": 2,
            "turn_pass_rate_pct": 0.0,
            "negative_suppression_rate_pct": 0.0,
        },
        "conversations": [
            {
                "conversation_id": "c1",
                "passed": False,
                "turns": [
                    {
                        "turn_id": 1,
                        "text": "Hello!",
                        "passed": False,
                        "failures": ["Expected negative suppression (silent turn), but triggered cards=['Caller Verification Card']"],
                    },
                    {
                        "turn_id": 2,
                        "text": "Where is my order?",
                        "passed": False,
                        "failures": ["Missed expected tool call: 'lookup_order' (got [])"],
                    },
                ],
            }
        ],
    }
    patterns = analyze_loss_patterns(fake_eval_report)
    categories = {p["category"] for p in patterns}
    assert "FALSE_POSITIVE_TRIGGER" in categories
    assert "MISSED_TOOL_CALL" in categories

    candidate, proposals = propose_bundle_improvements(bundle, patterns)
    assert len(proposals) == 2
    cards = candidate["companion_agent"]["skillConfigs"][0]["guidanceSkillConfig"]["guidanceInstructions"]
    assert cards[0]["triggerEvent"] == "CUSTOMER_MESSAGE"
    assert any("{@TOOL:lookup_order}" in a["description"] for a in cards[1]["actions"])
