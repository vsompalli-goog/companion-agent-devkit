"""Unit tests for deterministic best-practice configuration reviewer (FR-2.2)."""

from __future__ import annotations

from pathlib import Path

from aa_devkit.exporter import write_export_bundle
from aa_devkit.main import main
from aa_devkit.reviewer import review_bundle


def test_clean_bundle_produces_zero_findings() -> None:
    clean_bundle = {
        "manifest": {"schemaVersion": "1.0"},
        "conversation_profile": {
            "name": "projects/demo/locations/global/conversationProfiles/cp-1",
            "sttConfig": {"useLongFormModel": True, "useGeminiAsr": True},
            "humanAgentAssistantConfig": {
                "humanAgentSuggestionConfig": {
                    "companionAgent": "projects/demo/locations/global/companionAgents/ca-1",
                    "featureConfigs": [
                        {"suggestionFeature": {"type": "CONVERSATION_SUMMARIZATION"}},
                        {
                            "suggestionFeature": {"type": "KNOWLEDGE_ASSIST"},
                            "queryConfig": {"maxResults": 3},
                        },
                    ],
                }
            },
        },
        "companion_agent": {
            "name": "projects/demo/locations/global/companionAgents/ca-1",
            "displayName": "Clean Companion Agent",
            "cesToolSpecs": [
                {
                    "cesTool": "projects/demo/locations/global/apps/app-1/tools/lookup_account",
                    "confirmationRequirement": "NOT_REQUIRED",
                    "proactiveEnabled": True,
                    "reactiveEnabled": True,
                },
                {
                    "cesTool": "projects/demo/locations/global/apps/app-1/tools/refund_charge",
                    "confirmationRequirement": "REQUIRED",
                    "proactiveEnabled": True,
                    "reactiveEnabled": False,
                },
            ],
            "skillConfigs": [
                {
                    "skillTriggeringEvent": "END_OF_UTTERANCE",
                    "guidanceSkillConfig": {
                        "overarchingGuidance": (
                            "1. Role & Persona: Helpful agent co-pilot.\n"
                            "2. Channel Style & Brevity: Keep every response under 2 spoken sentences.\n"
                            "3. Priority Hierarchy: Safety -> Verification -> Resolution.\n"
                            "4. Global Completion & Guardrails: Verify identity before account actions."
                        ),
                        "guidanceInstructions": [
                            {
                                "displayName": "Identity Verification: Unauthenticated Caller",
                                "displayDetails": "Guides manual caller verification at the start of a call.",
                                "triggerEvent": "CUSTOMER_MESSAGE",
                                "condition": (
                                    'DEFAULT verification instruction. Apply unless CUSTOMERCONTEXT contains "ivr_authenticated": "true".'
                                ),
                                "actions": [
                                    {
                                        "description": (
                                            "Identify which required verification fields [Full Name, Account ID] the caller already stated. "
                                            "Without asking for their SSN, ask only for missing fields in under 2 sentences."
                                        )
                                    },
                                    {
                                        "description": (
                                            "Once verified, invoke {@TOOL:lookup_account} and summarize status in under 2 sentences."
                                        )
                                    },
                                ],
                            }
                        ],
                    },
                }
            ],
        },
        "workflows": [
            {
                "name": "projects/demo/locations/global/companionAgentWorkflows/wf-1",
                "displayName": "Refund Workflow",
                "description": "Applies when the customer requests a refund for a billed charge.",
                "steps": [],
            }
        ],
        "tools": [
            {
                "name": "projects/demo/locations/global/apps/app-1/tools/lookup_account",
                "displayName": "lookup_account",
                "toolType": "PYTHON_FUNCTION",
                "confirmationRequirement": "NOT_REQUIRED",
                "pythonFunction": {"name": "lookup_account"},
                "pythonCode": (
                    "def lookup_account(account_id: str = '') -> dict:\n"
                    '    """Looks up account.\n\n    Args:\n        account_id: Account identifier.\n\n    Returns:\n        Account dictionary.\n    """\n'
                    "    return {'id': account_id}\n"
                ),
            },
            {
                "name": "projects/demo/locations/global/apps/app-1/tools/refund_charge",
                "displayName": "refund_charge",
                "toolType": "PYTHON_FUNCTION",
                "confirmationRequirement": "REQUIRED",
                "pythonFunction": {"name": "refund_charge"},
                "pythonCode": (
                    "def refund_charge(charge_id: str = '') -> dict:\n"
                    '    """Refunds charge.\n\n    Args:\n        charge_id: Charge identifier.\n\n    Returns:\n        Refund confirmation.\n    """\n'
                    "    return {'status': 'REFUNDED'}\n"
                ),
            },
        ],
    }

    findings = review_bundle(clean_bundle)
    assert findings == []


def test_flawed_bundle_detects_all_rule_categories(tmp_path: Path) -> None:
    flawed_bundle = {
        "manifest": {"schemaVersion": "1.0"},
        "conversation_profile": {
            "name": "projects/demo/locations/global/conversationProfiles/cp-bad",
            "sttConfig": {"useLongFormModel": False, "useGeminiAsr": False},
            "humanAgentAssistantConfig": {
                "humanAgentSuggestionConfig": {
                    "companionAgent": "projects/demo/locations/global/companionAgents/ca-bad",
                    "featureConfigs": [
                        {"suggestionFeature": {"type": "KNOWLEDGE_ASSIST"}}
                    ],
                }
            },
        },
        "companion_agent": {
            "name": "projects/demo/locations/global/companionAgents/ca-bad",
            "displayName": "Flawed Companion Agent",
            "cesToolSpecs": [],
            "skillConfigs": [
                {
                    "skillTriggeringEvent": "END_OF_UTTERANCE",
                    "guidanceSkillConfig": {
                        "overarchingGuidance": "Always call {@TOOL:unlinked_tool} immediately.",
                        "guidanceInstructions": [
                            {
                                "displayName": "Caller Verification (Do NOT suggest during initial greetings)",
                                "displayDetails": "Must invoke {@TOOL:unlinked_tool} only if CUSTOMERCONTEXT is missing.",
                                "condition": "Do NOT suggest during greetings. Apply if customerContext is missing.",
                                "actions": [
                                    {"description": "Do NOT ask for the caller's SSN."},
                                    {"description": "Keep your response under 2 sentences."},
                                    {
                                        "description": (
                                            "If the caller is VIP, waive the fee; otherwise invoke {$tool.legacy_lookup} "
                                            "and {@TOOL:missing_tool}."
                                        )
                                    },
                                ],
                            },
                            {
                                "displayName": "IVR Exception Card",
                                "displayDetails": "Short summary.",
                                "triggerEvent": "CUSTOMER_MESSAGE",
                                "condition": 'EXCEPTION card. Apply ONLY if CUSTOMERCONTEXT contains "ivr": "true".',
                                "actions": [
                                    {
                                        "description": "Greet the caller by their preferred name from the call context."
                                    }
                                ],
                            },
                        ],
                    },
                }
            ],
        },
        "workflows": [
            {
                "name": "projects/demo/locations/global/companionAgentWorkflows/wf-bad",
                "displayName": "Bad Workflow",
                "description": "Do NOT trigger during initial greetings.",
            }
        ],
        "tools": [
            {
                "name": "projects/demo/locations/global/apps/app-1/tools/cancel_subscription",
                "displayName": "cancel_subscription",
                "toolType": "PYTHON_FUNCTION",
                "confirmationRequirement": "NOT_REQUIRED",
                "pythonFunction": {"name": "cancel_subscription"},
                "pythonCode": "def wrong_function_name(sub_id):\n    return {}\n",
            }
        ],
    }

    findings = review_bundle(flawed_bundle)
    rule_ids = {f.rule_id for f in findings}

    assert "CA-P0-001" in rule_ids  # Legacy {$tool...} and unlinked {@TOOL:...}
    assert "CA-P0-002" in rule_ids  # displayDetails logic leak
    assert "CA-P0-003" in rule_ids  # Checkpoint Rule standalone negative/formatting steps
    assert "CA-P0-004" in rule_ids  # Python function name mismatch
    assert "CA-P1-001" in rule_ids  # IF/ELSE branching inside a single card
    assert "CA-P1-002" in rule_ids  # Negative trigger words & missing CUSTOMER_MESSAGE
    assert "CA-P1-003" in rule_ids  # <call_context> absence gating & attractor phrase
    assert "CA-P1-004" in rule_ids  # Missing CONVERSATION_SUMMARIZATION & KNOWLEDGE_ASSIST queryConfig
    assert "CA-P1-005" in rule_ids  # cancel_subscription without confirmationRequirement=REQUIRED
    assert "CA-P2-001" in rule_ids  # overarchingGuidance missing 4-section structure

    # Verify CLI `aa-devkit review` against a .zip archive
    zip_path = tmp_path / "flawed_bundle.zip"
    write_export_bundle(flawed_bundle, zip_path)
    report_path = tmp_path / "report.md"

    exit_code = main(["review", str(zip_path), "--output", str(report_path), "--fail-on-p0"])
    assert exit_code == 1
    assert report_path.is_file()
    report_md = report_path.read_text(encoding="utf-8")
    assert "Critical Blockers (P0)" in report_md
    assert "CA-P0-001" in report_md
