"""Tests for all 6 Companion Agent Simulator capabilities integrated into `aa-devkit`:
  1. 3-Stage Universal Transcript Importer & Consistent `[REDACTED]` PII Healer (`import-transcript`)
  2. Config-Aware Synthetic Scenario Generator with Workflow Pacing (`generate-scenarios`)
  3. Layer 0 Cross-Turn State-Machine Validator & Regression Delta (`eval --bundle --baseline-run`)
  4. Session-Level Strict vs. Forgiving F1 Metrics & Ground-Truth Bootstrapper (`bootstrap-eval`)
  5. 5D Dynamic Customer Persona & 4-Strategy Agent Perturbation Harness (`simulate`)
  6. Telephony DSP Audio Conditioning & Prosody Normalization (`voice-synth`)
"""

from __future__ import annotations

import json
from pathlib import Path
import wave

from aa_devkit.eval_pipeline import (
    PayloadIntegrityValidator,
    bootstrap_eval_dataset,
    evaluate_dataset,
    flatten_nested_entities,
)
from aa_devkit.exporter import load_export_bundle
from aa_devkit.main import main
from aa_devkit.scenario_generator import (
    apply_agent_strategy,
    build_5d_persona_directive,
    compute_workflow_paced_turn_count,
    generate_scenarios_from_bundle,
    init_5d_persona_state,
    update_5d_persona_state,
)
from aa_devkit.transcript_importer import (
    import_transcript,
    import_transcript_file,
)
from aa_devkit.voice_tester import (
    StreamingProfile,
    apply_telephone_bandpass,
    generate_transfer_ringtone_pcm,
    interleave_stereo_pcm,
    overlay_ambient_noise,
    prepare_text_for_natural_tts,
    split_text_into_tts_chunks,
    synthesize_transcript_audio,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_stage1_genesys_amazon_ccai_and_pii_healer() -> None:
    """Verifies Stage 1 JSON fingerprinting, consecutive turn merging, and consistent PII healing."""
    genesys_path = REPO_ROOT / "evals" / "samples" / "sample_genesys_transcript.json"
    dataset = import_transcript_file(genesys_path)
    meta = dataset["importMetadata"]
    assert meta["detectedFormat"] == "genesys_cloud"
    assert meta["piiHealer"]["total_redactions_healed"] >= 6

    turns = dataset["conversations"][0]["turns"]
    # Two consecutive external customer phrases in Turn 3 & 4 should be merged into 1 turn
    assert len(turns) == 5
    # Verify [REDACTED] placeholders were healed consistently across caller and agent turns
    caller_turn = turns[2]["text"]
    agent_turn = turns[3]["text"]
    assert "[REDACTED" not in caller_turn
    assert "[REDACTED" not in agent_turn
    assert "ORD-849201" in caller_turn and "ORD-849201" in agent_turn
    assert "alex.rivera@example.com" in caller_turn and "alex.rivera@example.com" in agent_turn
    # Greeting turn auto-tagged with must_suppress=True
    assert turns[0]["expected"]["must_suppress"] is True

    # Amazon Connect Contact Lens format
    amazon_raw = json.dumps(
        {
            "Transcript": [
                {"ParticipantId": "CUSTOMER", "Content": "Hi"},
                {"ParticipantId": "AGENT", "Content": "Hello, what is your order number?"},
                {"ParticipantId": "CUSTOMER", "Content": "My order number is [REDACTED] and last 4 SSN is ****."},
            ]
        }
    )
    amz_ds = import_transcript(amazon_raw, agent_mode="SUGGESTION")
    assert amz_ds["importMetadata"]["detectedFormat"] == "amazon_connect_contact_lens"
    amz_turns = amz_ds["conversations"][0]["turns"]
    assert "Follow the Companion Agent's suggestion" in amz_turns[1]["text"]
    assert "ORD-849201" in amz_turns[2]["text"]
    assert "4829" in amz_turns[2]["text"]


def test_stage2_csv_and_timestamped_call_log() -> None:
    """Verifies Stage 2 CSV/TSV and timestamped call-log regex heuristics."""
    call_log_path = REPO_ROOT / "evals" / "samples" / "sample_call_log.txt"
    ds = import_transcript_file(call_log_path)
    assert ds["importMetadata"]["detectedFormat"] == "timestamped_call_log"
    turns = ds["conversations"][0]["turns"]
    assert len(turns) == 5
    # Caller and Agent turns share the exact same healed order number and phone number
    assert "ORD-849201" in turns[2]["text"] and "ORD-849201" in turns[3]["text"]
    assert "415-555-0192" in turns[2]["text"] and "415-555-0192" in turns[3]["text"]

    csv_raw = "speaker,utterance\nCustomer,Hello!\nAgent,How can I help you with order [REDACTED_ORDER]?\n"
    csv_ds = import_transcript(csv_raw)
    assert csv_ds["importMetadata"]["detectedFormat"] == "csv_tsv"
    assert "ORD-849201" in csv_ds["conversations"][0]["turns"][1]["text"]


def test_config_aware_scenario_generator_and_workflow_pacing() -> None:
    """Verifies Multi-Step Workflow Pacing (1 step = 2 turns) and Negative Suppression turns."""
    assert compute_workflow_paced_turn_count(requested_turns=4, max_workflow_steps=3) == 8
    bundle = load_export_bundle(REPO_ROOT / "evals" / "samples" / "sample_agent_bundle")
    ds = generate_scenarios_from_bundle(bundle, count=2, requested_turns=4)
    meta = ds["generatorMetadata"]
    assert meta["maxWorkflowSteps"] == 3
    assert meta["effectivePacedTurns"] == 8
    assert len(ds["conversations"]) == 2

    first_conv = ds["conversations"][0]
    assert len(first_conv["turns"]) == 8
    # Opening greeting and closing thank-you turns must have must_suppress=True
    assert first_conv["turns"][0]["expected"]["must_suppress"] is True
    assert first_conv["turns"][-1]["expected"]["must_suppress"] is True


def test_layer0_cross_turn_state_machine_validator() -> None:
    """Verifies Layer 0 cross-turn backward state transition and UNCONFIRMED_EXECUTION_BYPASS detection."""
    validator = PayloadIntegrityValidator(confirmation_required_tools={"issue_refund"})

    # Turn 1: issue_refund jumps straight to COMPLETED without prior NEEDS_CONFIRMATION -> Engine Bug!
    t1_resp = {
        "humanAgentSuggestionResults": [
            {
                "generateCompanionSuggestionsResponse": {
                    "companionSuggestion": {
                        "knowledgeSources": [{"snippets": []}],
                        "guidances": [
                            {
                                "displayName": "Damaged Item Refund Confirmation",
                                "toolCalls": [
                                    {
                                        "toolCall": {
                                            "toolDisplayName": "issue_refund",
                                            "state": "COMPLETED",
                                            "answerRecord": "ar-refund-1",
                                            "inputParameters": {"order_id": "ORD-849201", "amount": 49.99},
                                        }
                                    }
                                ],
                            }
                        ],
                    }
                }
            }
        ]
    }
    anomalies_t1 = validator.validate_turn(1, t1_resp)
    assert any("UNCONFIRMED_EXECUTION_BYPASS" in a for a in anomalies_t1)
    assert any("CITATION_ANOMALY" in a for a in anomalies_t1)

    # Turn 2: ar-refund-1 transitions backward from COMPLETED -> NEEDS_CONFIRMATION -> State Machine Bug!
    t2_resp = {
        "humanAgentSuggestionResults": [
            {
                "generateCompanionSuggestionsResponse": {
                    "companionSuggestion": {
                        "guidances": [
                            {
                                "displayName": "Damaged Item Refund Confirmation",
                                "toolCalls": [
                                    {
                                        "toolCall": {
                                            "toolDisplayName": "issue_refund",
                                            "state": "NEEDS_CONFIRMATION",
                                            "answerRecord": "ar-refund-1",
                                        }
                                    }
                                ],
                            }
                        ]
                    }
                }
            }
        ]
    }
    anomalies_t2 = validator.validate_turn(2, t2_resp)
    assert any("STATE_MACHINE_BUG" in a for a in anomalies_t2)


def test_strict_vs_forgiving_f1_and_bootstrap_eval(tmp_path: Path) -> None:
    """Verifies nested entity flattening, Strict vs. Forgiving F1, carryover memory, and bootstrap-eval."""
    nested = {
        "customer": {"address": {"zip": "94107"}, "tags": ["vip", "gold"]},
        "tool_description": "ignored internal field",
        "_extraction_reasoning": "ignored reasoning",
    }
    flat = flatten_nested_entities(nested)
    assert flat == {
        "customer.address.zip": "94107",
        "customer.tags.0": "vip",
        "customer.tags.1": "gold",
    }

    sample_ds_path = REPO_ROOT / "evals" / "samples" / "sample_eval_conversations.json"
    report = evaluate_dataset(sample_ds_path, baseline_run_path=sample_ds_path)
    em = report["summary"]["entity_metrics"]
    assert em["strict"]["f1_pct"] == 100.0
    assert em["forgiving"]["f1_pct"] == 100.0
    assert report["baseline_comparison"]["turn_pass_rate_delta_pct"] == 0.0

    # Bootstrap ground-truth expected blocks from recorded actual_response
    bootstrapped = bootstrap_eval_dataset(sample_ds_path)
    assert bootstrapped["bootstrapMetadata"]["turnsBootstrapped"] == 2
    boot_path = tmp_path / "bootstrapped.json"
    boot_path.write_text(json.dumps(bootstrapped, indent=2), encoding="utf-8")
    boot_eval = evaluate_dataset(boot_path)
    assert boot_eval["summary"]["turn_pass_rate_pct"] == 100.0


def test_5d_persona_and_4_strategy_agent_perturbation() -> None:
    """Verifies 5D Customer Persona patience decay, tone escalation, and 4 agent strategies."""
    state = init_5d_persona_state(
        tone="polite",
        tech_literacy="low",
        patience=3,
        escalation_tendency="high",
        verbosity="concise",
    )
    directive = build_5d_persona_directive(
        state, {"order_id": "ORD-849201", "email": "alex.rivera@example.com"}
    )
    assert "AT MOST 1 requested detail" in directive

    # Stall turn 1 -> patience drops from 3 to 2, tone shifts from polite -> frustrated
    update_5d_persona_state(state, "One more moment, system is slow.")
    assert state.current_patience == 2
    assert state.current_tone == "frustrated"

    # Repetitive turn 2 -> patience drops from 2 to 1, tone shifts to adversarial + escalation_requested
    update_5d_persona_state(
        state,
        "One more moment, system is slow.",
        previous_agent_utterance="One more moment, system is slow.",
    )
    assert state.current_patience == 1
    assert state.current_tone == "adversarial"
    assert state.escalation_requested is True

    # Verify all 4 Virtual Human Agent perturbation strategies
    s_strict = apply_agent_strategy("Can I get your order ID?", "strict")
    assert s_strict["text"] == "Can I get your order ID?"

    s_para = apply_agent_strategy("Can I get your order ID?", "paraphrase", requested_slot="order_id")
    assert "order id" in s_para["text"].lower()

    s_dev = apply_agent_strategy("Can I get your order ID?", "deviate", guidance_card_title="Order Lookup")
    assert s_dev["expects_recovery_on_next_turn"] is True

    s_neg = apply_agent_strategy("Can I get your order ID?", "probe_negative")
    assert s_neg["injected_negative_probe"] is True


def test_telephony_dsp_and_voice_synth_cli(tmp_path: Path) -> None:
    """Verifies TTS prosody normalization, sentence chunking, G.711 bandpass, noise, ringtone, and stereo WAV."""
    cleaned = prepare_text_for_natural_tts(
        '"Hello! [sighs] I need help with **order** `ORD-849201` (frustrated tone)"'
    )
    assert cleaned == "Hello! I need help with order ORD-849201."

    long_text = (
        "Thank you for calling Acme Customer Support today. "
        "I can see that your replacement package was shipped via expedited air freight this morning, "
        "and your updated tracking number has been sent to your email address on file. "
        "Please let me know if there is anything else I can assist you with today."
    )
    chunks = split_text_into_tts_chunks(long_text, max_chars=120)
    assert len(chunks) >= 2
    assert all(len(c) <= 120 for c in chunks)

    ring_pcm = generate_transfer_ringtone_pcm(duration_sec=0.5, sample_rate_hz=8000)
    filtered_pcm = apply_telephone_bandpass(ring_pcm, sample_rate_hz=8000)
    noisy_pcm = overlay_ambient_noise(filtered_pcm, noise_profile="call_center", sample_rate_hz=8000)
    stereo_pcm = interleave_stereo_pcm(noisy_pcm, ring_pcm)
    assert len(stereo_pcm) == len(ring_pcm) * 2

    out_wav_dir = tmp_path / "voice_out"
    rc = main(
        [
            "voice-synth",
            str(REPO_ROOT / "evals" / "samples" / "sample_eval_conversations.json"),
            "--output-dir",
            str(out_wav_dir),
            "--offline",
            "--telephone-filter",
            "--noise-profile",
            "call_center",
            "--stereo",
            "--include-transfer-ring",
        ]
    )
    assert rc == 0
    manifest = json.loads((out_wav_dir / "voice_manifest.json").read_text(encoding="utf-8"))
    assert manifest["derivedFromCustomerData"] is True
    assert manifest["voiceCloningUsed"] is False
    assert manifest["stereoFullCallFile"] == "full_call_stereo.wav"
    assert (out_wav_dir / "turn_000_transfer_ring.wav").exists()
    with wave.open(str(out_wav_dir / "full_call_stereo.wav"), "rb") as wf:
        assert wf.getnchannels() == 2


def test_cli_subcommands_end_to_end(tmp_path: Path) -> None:
    """Runs CLI subcommands (`import-transcript`, `generate-scenarios`, `simulate`, `bootstrap-eval`) end-to-end."""
    imported_out = tmp_path / "imported.json"
    assert (
        main(
            [
                "import-transcript",
                str(REPO_ROOT / "evals" / "samples" / "sample_genesys_transcript.json"),
                "--output",
                str(imported_out),
            ]
        )
        == 0
    )
    assert imported_out.exists()

    scenarios_out = tmp_path / "scenarios.json"
    assert (
        main(
            [
                "generate-scenarios",
                "--bundle",
                str(REPO_ROOT / "evals" / "samples" / "sample_agent_bundle"),
                "--count",
                "2",
                "--turns",
                "6",
                "--output",
                str(scenarios_out),
            ]
        )
        == 0
    )
    assert scenarios_out.exists()

    sim_out = tmp_path / "simulated.json"
    assert (
        main(
            [
                "simulate",
                "--bundle",
                str(REPO_ROOT / "evals" / "samples" / "sample_agent_bundle"),
                "--tone",
                "confused",
                "--tech-literacy",
                "low",
                "--agent-strategy",
                "paraphrase",
                "--output",
                str(sim_out),
            ]
        )
        == 0
    )
    sim_data = json.loads(sim_out.read_text(encoding="utf-8"))
    assert sim_data["conversations"][0]["persona_5d_final"]["tech_literacy"] == "low"
