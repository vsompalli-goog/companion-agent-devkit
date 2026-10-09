"""Stateless CLI entrypoint (`aa-devkit`) for Companion Agent DevKit (L1 – L4 + Simulator Suite)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Optional

from aa_devkit.client import CompanionAgentRestClient, parse_resource_path
from aa_devkit.eval_pipeline import (
    bootstrap_eval_dataset,
    evaluate_dataset,
    format_eval_markdown,
)
from aa_devkit.exporter import load_export_bundle, write_export_bundle
from aa_devkit.hillclimb import (
    analyze_loss_patterns,
    estimate_eval_cost,
    format_hillclimb_markdown,
    propose_bundle_improvements,
)
from aa_devkit.latency import (
    analyze_latency_dataset,
    format_latency_html_dashboard,
    format_latency_markdown,
)
from aa_devkit.migrator import migrate_ai_coach_to_companion_agent
from aa_devkit.pack_builder import install_pack
from aa_devkit.reviewer import (
    format_review_json,
    format_review_markdown,
    review_bundle,
)
from aa_devkit.scenario_generator import (
    AGENT_STRATEGIES,
    TONE_GUIDANCE,
    VERBOSITY_GUIDANCE,
    build_5d_persona_directive,
    generate_scenarios_from_bundle,
    init_5d_persona_state,
    update_5d_persona_state,
)
from aa_devkit.skill_eval import run_skill_regression_eval
from aa_devkit.transcript_importer import import_transcript_file
from aa_devkit.voice_tester import (
    ALLOWED_NOISE_PROFILES,
    ALLOWED_STOCK_VOICES,
    StreamingProfile,
    replay_voice_manifest,
    synthesize_transcript_audio,
)


def _write_or_print(output_text: str, output_path: Optional[str]) -> None:
    if output_path:
        out_file = Path(output_path).expanduser().resolve()
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(output_text, encoding="utf-8")
        print(f"Wrote output to {out_file}")
    else:
        sys.stdout.write(output_text)


def _cmd_install_pack(args: argparse.Namespace) -> int:
    installed = install_pack(
        dest_root=args.dest,
        target=args.target,
        pack_source=args.source,
    )
    print(f"Installed {len(installed)} customization artifact(s) into {args.dest}:")
    for p in installed:
        print(f"  - {p}")
    return 0


def _cmd_migrate_coach(args: argparse.Namespace) -> int:
    legacy_payload = json.loads(Path(args.input).expanduser().resolve().read_text(encoding="utf-8"))
    migrated = migrate_ai_coach_to_companion_agent(legacy_payload)
    output_text = json.dumps(migrated, indent=2) + "\n"
    _write_or_print(output_text, args.output)
    if args.output and migrated.get("migration_notes"):
        print(f"Migration notes ({len(migrated['migration_notes'])}):")
        for note in migrated["migration_notes"]:
            print(f"  - {note}")
    return 0


def _cmd_eval_skills(args: argparse.Namespace) -> int:
    report = run_skill_regression_eval(
        golden_path=args.golden,
        pack_source=args.source,
        candidate_answers_path=args.answers,
    )
    if args.format == "json":
        output_text = json.dumps(report, indent=2) + "\n"
    else:
        lines = [
            "# Golden-Question Skill Regression Report (FR-1.5)",
            "",
            f"- **Pass Rate**: **{report['pass_rate_pct']}%** ({report['passed']}/{report['total']} golden questions)",
            "",
        ]
        for r in report["results"]:
            status = "PASS" if r["passed"] else "FAIL"
            lines.append(f"- `{r['id']}` ({r['fr']} — `{r['skill']}`): **{status}**")
            for err in r["errors"]:
                lines.append(f"  - {err}")
        output_text = "\n".join(lines) + "\n"

    _write_or_print(output_text, args.output)
    return 0 if report["failed"] == 0 else 1


def _cmd_export(args: argparse.Namespace) -> int:
    if not args.profile and not args.agent:
        print("Error: Must specify either --profile or --agent resource path.", file=sys.stderr)
        return 2

    primary_ref = args.profile or args.agent
    inferred_project, inferred_loc, _ = parse_resource_path(primary_ref)
    project_id = args.project or inferred_project
    location = args.location or inferred_loc or "global"

    if not project_id:
        print(
            "Error: Could not infer GCP project ID from resource path; pass --project explicitly.",
            file=sys.stderr,
        )
        return 2

    client = CompanionAgentRestClient(
        project_id=project_id,
        location=location,
        environment=args.env,
        quota_project_id=args.quota_project or project_id,
    )
    graph = client.harvest_configuration_graph(
        profile_name=args.profile,
        companion_agent_name=args.agent,
        include_tools=not args.skip_tools,
    )
    out_path = write_export_bundle(graph, args.output)
    print(
        f"Exported Companion Agent bundle ({graph['manifest']['workflowCount']} workflows, "
        f"{graph['manifest']['toolCount']} tools) to: {out_path}"
    )
    return 0


def _cmd_review(args: argparse.Namespace) -> int:
    bundle = load_export_bundle(args.bundle)
    findings = review_bundle(bundle)

    if args.format == "json":
        output_text = json.dumps(format_review_json(bundle, findings), indent=2) + "\n"
    else:
        output_text = format_review_markdown(bundle, findings)

    _write_or_print(output_text, args.output)

    if args.fail_on_p0 and any(f.severity == "P0" for f in findings):
        return 1
    return 0


def _cmd_import_transcript(args: argparse.Namespace) -> int:
    dataset = import_transcript_file(
        input_path=args.input,
        conversation_id=args.conversation_id,
        description=args.description,
        agent_mode=args.agent_mode,
        merge_consecutive=not args.no_merge_consecutive,
        heal_pii=not args.no_heal_pii,
    )
    output_text = json.dumps(dataset, indent=2) + "\n"
    _write_or_print(output_text, args.output)
    return 0


def _cmd_generate_scenarios(args: argparse.Namespace) -> int:
    bundle = load_export_bundle(args.bundle)
    dataset = generate_scenarios_from_bundle(
        bundle=bundle,
        count=args.count,
        requested_turns=args.turns,
        include_small_talk=not args.no_small_talk,
        persona_tone=args.tone,
        persona_tech_literacy=args.tech_literacy,
        agent_strategy=args.agent_strategy,
    )
    output_text = json.dumps(dataset, indent=2) + "\n"
    _write_or_print(output_text, args.output)
    return 0


def _cmd_bootstrap_eval(args: argparse.Namespace) -> int:
    bootstrapped = bootstrap_eval_dataset(args.dataset)
    output_text = json.dumps(bootstrapped, indent=2) + "\n"
    _write_or_print(output_text, args.output)
    return 0


def _cmd_simulate(args: argparse.Namespace) -> int:
    bundle = load_export_bundle(args.bundle)
    dataset = generate_scenarios_from_bundle(
        bundle=bundle,
        count=args.count,
        requested_turns=args.turns,
        include_small_talk=True,
        persona_tone=args.tone,
        persona_tech_literacy=args.tech_literacy,
        agent_strategy=args.agent_strategy,
    )
    # Attach 5D persona progression trace & directive preview to each simulated conversation
    for conv in dataset.get("conversations") or []:
        state = init_5d_persona_state(
            tone=args.tone,
            tech_literacy=args.tech_literacy,
            patience=args.patience,
            escalation_tendency=args.escalation_tendency,
            verbosity=args.verbosity,
        )
        prev_agent = ""
        for t in conv.get("turns") or []:
            if t.get("role") == "HUMAN_AGENT":
                update_5d_persona_state(state, t.get("text", ""), prev_agent)
                prev_agent = t.get("text", "")
        conv["persona_5d_final"] = {
            "initial_tone": state.initial_tone,
            "final_tone": state.current_tone,
            "initial_patience": state.initial_patience,
            "final_patience": state.current_patience,
            "tech_literacy": state.tech_literacy,
            "verbosity": state.verbosity,
            "escalation_tendency": state.escalation_tendency,
            "escalation_requested": state.escalation_requested,
            "tone_history": state.tone_history,
            "sample_directive": build_5d_persona_directive(
                state, {"order_id": "ORD-849201", "email": "alex.rivera@example.com"}
            ),
        }

    output_text = json.dumps(dataset, indent=2) + "\n"
    _write_or_print(output_text, args.output)
    return 0


def _cmd_eval(args: argparse.Namespace) -> int:
    client = None
    if args.live and args.profile:
        inferred_project, inferred_loc, _ = parse_resource_path(args.profile)
        project_id = args.project or inferred_project
        location = args.location or inferred_loc or "global"
        if not project_id:
            print("Error: --project or full --profile resource path required for --live.", file=sys.stderr)
            return 2
        client = CompanionAgentRestClient(
            project_id=project_id,
            location=location,
            environment=args.env,
            quota_project_id=args.quota_project or project_id,
        )

    bundle = load_export_bundle(args.bundle) if getattr(args, "bundle", None) else None
    report = evaluate_dataset(
        dataset_path=args.dataset,
        client=client,
        profile_name=args.profile,
        bundle=bundle,
        baseline_run_path=getattr(args, "baseline_run", None),
    )
    if args.format == "json":
        output_text = json.dumps(report, indent=2) + "\n"
    else:
        output_text = format_eval_markdown(report)

    _write_or_print(output_text, args.output)
    return 0 if report["summary"]["failed_turns"] == 0 else 1


def _cmd_voice_synth(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.transcript).expanduser().resolve().read_text(encoding="utf-8"))
    turns = data.get("turns")
    if turns is None and data.get("conversations"):
        turns = data["conversations"][0].get("turns") or []

    profile = StreamingProfile(
        codec=args.codec,
        sample_rate_hz=args.sample_rate_hz,
        chunk_ms=args.chunk_ms,
        cadence_ms=args.cadence_ms,
        jitter_ms=args.jitter_ms,
        single_utterance_endpointing=args.single_utterance,
        telephone_filter=args.telephone_filter,
        noise_profile=args.noise_profile,
        stereo=args.stereo,
        include_transfer_ring=args.include_transfer_ring,
    )
    client = None
    if not args.offline:
        if not args.project:
            print("Error: --project is required unless --offline is specified.", file=sys.stderr)
            return 2
        client = CompanionAgentRestClient(
            project_id=args.project,
            location=args.location,
            quota_project_id=args.quota_project or args.project,
        )

    manifest = synthesize_transcript_audio(
        client=client,
        turns=turns or [],
        output_dir=args.output_dir,
        caller_voice=args.caller_voice,
        agent_voice=args.agent_voice,
        streaming_profile=profile,
        offline_fallback=args.offline,
    )
    print(
        f"Synthesized {len(manifest['turns'])} turn(s) using stock voices only "
        f"(derivedFromCustomerData={manifest['derivedFromCustomerData']}, "
        f"telephoneFilter={profile.telephone_filter}, noiseProfile={profile.noise_profile}, "
        f"stereo={profile.stereo}) into {args.output_dir}"
    )
    return 0


def _cmd_voice_replay(args: argparse.Namespace) -> int:
    inferred_project, inferred_loc, _ = parse_resource_path(args.profile)
    project_id = args.project or inferred_project
    location = args.location or inferred_loc or "global"
    if not project_id:
        print("Error: --project or full --profile resource path required.", file=sys.stderr)
        return 2

    client = CompanionAgentRestClient(
        project_id=project_id,
        location=location,
        environment=args.env,
        quota_project_id=args.quota_project or project_id,
    )
    result = replay_voice_manifest(
        client=client,
        manifest_dir=args.manifest_dir,
        profile_name=args.profile,
        simulate_pacing=args.simulate_pacing,
    )
    output_text = json.dumps(result, indent=2) + "\n"
    _write_or_print(output_text, args.output)
    return 0


def _cmd_latency_report(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.dataset).expanduser().resolve().read_text(encoding="utf-8"))
    report = analyze_latency_dataset(data)
    if args.format == "json":
        output_text = json.dumps(report, indent=2) + "\n"
    elif args.format == "html":
        output_text = format_latency_html_dashboard(report)
    else:
        output_text = format_latency_markdown(report)

    _write_or_print(output_text, args.output)
    return 0


def _cmd_hillclimb(args: argparse.Namespace) -> int:
    bundle = load_export_bundle(args.bundle)
    dataset = json.loads(Path(args.dataset).expanduser().resolve().read_text(encoding="utf-8"))
    cost_est = estimate_eval_cost(
        dataset=dataset,
        local_bundle=bundle,
        iterations=args.iterations,
    )

    if args.estimate_only:
        output_text = json.dumps(cost_est, indent=2) + "\n"
        _write_or_print(output_text, args.output)
        return 0

    eval_report = evaluate_dataset(dataset_path=args.dataset, bundle=bundle)
    loss_patterns = analyze_loss_patterns(eval_report)
    candidate_bundle, applied_proposals = propose_bundle_improvements(bundle, loss_patterns)

    candidate_dir_out = None
    if args.candidate_output:
        candidate_dir_out = write_export_bundle(candidate_bundle, args.candidate_output)

    if args.format == "json":
        payload = {
            "cost_estimate": cost_est,
            "eval_summary": eval_report["summary"],
            "loss_patterns": loss_patterns,
            "applied_proposals": applied_proposals,
            "candidate_output": str(candidate_dir_out) if candidate_dir_out else None,
        }
        output_text = json.dumps(payload, indent=2) + "\n"
    else:
        output_text = format_hillclimb_markdown(
            cost_estimate=cost_est,
            eval_report=eval_report,
            loss_patterns=loss_patterns,
            applied_proposals=applied_proposals,
            candidate_dir=candidate_dir_out,
        )

    _write_or_print(output_text, args.output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aa-devkit",
        description="Stateless CLI for Agent Assist Companion Agent DevKit (L1 – L4 + Simulator Suite).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # install-pack (FR-1.1)
    p_install = subparsers.add_parser(
        "install-pack",
        help="Install single-source skills & rules pack into .agent or .claude folders (FR-1.1).",
    )
    p_install.add_argument(
        "--target",
        choices=["agent", "claude", "all"],
        default="agent",
        help="Target AI coding agent format: 'agent' (.agent), 'claude' (.claude), or 'all'.",
    )
    p_install.add_argument(
        "--dest",
        default=".",
        help="Destination workspace root directory (default: current directory).",
    )
    p_install.add_argument(
        "--source",
        default=None,
        help="Optional override path to single-source pack/ directory.",
    )
    p_install.set_defaults(func=_cmd_install_pack)

    # migrate-coach (FR-1.3)
    p_migrate = subparsers.add_parser(
        "migrate-coach",
        help="Convert legacy AI Coach (agentCoachingContext) / PGKA JSON into Companion Agent JSON (FR-1.3).",
    )
    p_migrate.add_argument("input", help="Path to legacy AI Coach Generator JSON file.")
    p_migrate.add_argument("--output", help="Optional path to write migrated Companion Agent JSON.")
    p_migrate.set_defaults(func=_cmd_migrate_coach)

    # eval-skills (FR-1.5)
    p_eval_skills = subparsers.add_parser(
        "eval-skills",
        help="Run golden-question regression evaluation over DevKit skills (FR-1.5).",
    )
    p_eval_skills.add_argument("--golden", help="Optional override path to golden_questions.json.")
    p_eval_skills.add_argument("--source", help="Optional override path to pack/ directory.")
    p_eval_skills.add_argument(
        "--answers",
        help="Optional JSON mapping question IDs (e.g. GQ-001) to candidate model answers.",
    )
    p_eval_skills.add_argument("--format", choices=["markdown", "json"], default="markdown")
    p_eval_skills.add_argument("--output", help="Optional file path to write report.")
    p_eval_skills.set_defaults(func=_cmd_eval_skills)

    # export (FR-2.1)
    p_export = subparsers.add_parser(
        "export",
        help="Statelessly export ConversationProfile, CompanionAgent, Workflows, and Tools to a local folder or .zip (FR-2.1).",
    )
    p_export.add_argument(
        "--profile",
        help="Full ConversationProfile resource name (projects/<proj>/locations/<loc>/conversationProfiles/<id>).",
    )
    p_export.add_argument(
        "--agent",
        help="Full CompanionAgent resource name (projects/<proj>/locations/<loc>/companionAgents/<id>).",
    )
    p_export.add_argument("--project", help="Optional GCP project ID override.")
    p_export.add_argument("--location", help="Optional GCP location override.")
    p_export.add_argument("--quota-project", help="Optional X-Goog-User-Project override.")
    p_export.add_argument("--env", choices=["prod", "staging"], default="prod")
    p_export.add_argument("--skip-tools", action="store_true", help="Skip fetching individual tool definitions.")
    p_export.add_argument(
        "--output",
        required=True,
        help="Output path: local directory (e.g. ./bundle/) or .zip archive (e.g. ./bundle.zip).",
    )
    p_export.set_defaults(func=_cmd_export)

    # review (FR-2.2)
    p_review = subparsers.add_parser(
        "review",
        help="Run deterministic best-practice config review against a local exported folder or .zip (FR-2.2).",
    )
    p_review.add_argument("bundle", help="Path to local exported configuration folder or .zip archive.")
    p_review.add_argument("--format", choices=["markdown", "json"], default="markdown")
    p_review.add_argument("--output", help="Optional file path to write the review report.")
    p_review.add_argument(
        "--fail-on-p0",
        action="store_true",
        help="Return exit code 1 if any P0 critical blocker is found.",
    )
    p_review.set_defaults(func=_cmd_review)

    # import-transcript (Simulator Capability #1)
    p_import = subparsers.add_parser(
        "import-transcript",
        help="3-Stage Universal Transcript Importer & Consistent [REDACTED] PII Healer (Genesys, Amazon Connect, CCAI, CSV/TSV, Call Logs).",
    )
    p_import.add_argument("input", help="Path to raw transcript file (JSON, CSV, TSV, or TXT).")
    p_import.add_argument("--conversation-id", help="Optional conversation_id override.")
    p_import.add_argument("--description", help="Optional description override.")
    p_import.add_argument(
        "--agent-mode",
        choices=["TRANSCRIPT", "SUGGESTION"],
        default="TRANSCRIPT",
        help="Keep original agent turns (TRANSCRIPT) or convert to dynamic suggestion-following mode (SUGGESTION).",
    )
    p_import.add_argument(
        "--no-merge-consecutive",
        action="store_true",
        help="Do not merge consecutive turns from the same speaker.",
    )
    p_import.add_argument(
        "--no-heal-pii",
        action="store_true",
        help="Disable Stage 3 [REDACTED] / [PII] placeholder healing.",
    )
    p_import.add_argument("--output", help="Optional file path to write imported FR-4.2 evaluation dataset JSON.")
    p_import.set_defaults(func=_cmd_import_transcript)

    # generate-scenarios (Simulator Capability #2)
    p_gen = subparsers.add_parser(
        "generate-scenarios",
        help="Config-Aware Synthetic Scenario Generator enforcing Multi-Step Workflow Pacing (1 step = 2 turns) and Negative Suppression.",
    )
    p_gen.add_argument("--bundle", required=True, help="Path to local exported CompanionAgent bundle directory or .zip.")
    p_gen.add_argument("--count", type=int, default=2, help="Number of synthetic conversations to generate.")
    p_gen.add_argument(
        "--turns",
        type=int,
        default=6,
        help="Requested minimum turns per scenario (auto-expanded if workflow step count requires more turns).",
    )
    p_gen.add_argument(
        "--no-small-talk",
        action="store_true",
        help="Omit opening/closing negative-suppression small-talk turns.",
    )
    p_gen.add_argument("--tone", choices=sorted(TONE_GUIDANCE.keys()), default="polite")
    p_gen.add_argument("--tech-literacy", choices=["low", "medium", "high"], default="medium")
    p_gen.add_argument("--agent-strategy", choices=list(AGENT_STRATEGIES), default="strict")
    p_gen.add_argument("--output", help="Optional file path to write generated FR-4.2 evaluation dataset JSON.")
    p_gen.set_defaults(func=_cmd_generate_scenarios)

    # bootstrap-eval (Simulator Capability #4)
    p_boot = subparsers.add_parser(
        "bootstrap-eval",
        help="Reverse-engineer ground-truth expected_entities, expected_guidance_cards, and expected_tools from recorded actual_response payloads.",
    )
    p_boot.add_argument("dataset", help="Path to dataset JSON containing recorded actual_response payloads.")
    p_boot.add_argument("--output", help="Optional file path to write bootstrapped dataset JSON.")
    p_boot.set_defaults(func=_cmd_bootstrap_eval)

    # simulate (Simulator Capability #5 / FR-5.5)
    p_sim = subparsers.add_parser(
        "simulate",
        help="Run 5D Dynamic Customer Persona & 4-Strategy Virtual Human Agent Perturbation Harness (FR-5.5).",
    )
    p_sim.add_argument("--bundle", required=True, help="Path to local exported CompanionAgent bundle.")
    p_sim.add_argument("--count", type=int, default=1, help="Number of simulated conversations.")
    p_sim.add_argument("--turns", type=int, default=8, help="Target turns per simulated conversation.")
    p_sim.add_argument("--tone", choices=sorted(TONE_GUIDANCE.keys()), default="polite", help="Initial 5D caller tone.")
    p_sim.add_argument(
        "--tech-literacy",
        choices=["low", "medium", "high"],
        default="medium",
        help="5D caller tech literacy (controls slot drip-feeding rate).",
    )
    p_sim.add_argument("--patience", type=int, default=4, help="Initial 5D caller patience (1-5).")
    p_sim.add_argument(
        "--escalation-tendency",
        choices=["low", "medium", "high"],
        default="medium",
        help="5D caller supervisor escalation tendency.",
    )
    p_sim.add_argument(
        "--verbosity",
        choices=sorted(VERBOSITY_GUIDANCE.keys()),
        default="normal",
        help="5D caller utterance verbosity.",
    )
    p_sim.add_argument(
        "--agent-strategy",
        choices=list(AGENT_STRATEGIES),
        default="strict",
        help="Virtual Human Agent strategy: strict, paraphrase, deviate, or probe_negative.",
    )
    p_sim.add_argument("--output", help="Optional file path to write simulated dataset JSON.")
    p_sim.set_defaults(func=_cmd_simulate)

    # eval (FR-4.1, FR-4.2 + Simulator Capabilities #3 & #4)
    p_eval = subparsers.add_parser(
        "eval",
        help="Run turn-by-turn Companion Agent evaluation pipeline with Layer 0 state-machine checks and Strict/Forgiving F1 (FR-4.1, FR-4.2).",
    )
    p_eval.add_argument("dataset", help="Path to turn-by-turn evaluation JSON dataset.")
    p_eval.add_argument("--bundle", help="Optional exported bundle path to enforce confirmationRequirement=REQUIRED checks.")
    p_eval.add_argument("--baseline-run", help="Optional prior evaluation report or dataset JSON to compute regression deltas.")
    p_eval.add_argument("--live", action="store_true", help="Execute live :analyzeContent calls against GCP.")
    p_eval.add_argument("--profile", help="Full ConversationProfile resource path (required with --live).")
    p_eval.add_argument("--project", help="Optional GCP project ID override.")
    p_eval.add_argument("--location", help="Optional GCP location override.")
    p_eval.add_argument("--quota-project", help="Optional X-Goog-User-Project override.")
    p_eval.add_argument("--env", choices=["prod", "staging"], default="prod")
    p_eval.add_argument("--format", choices=["markdown", "json"], default="markdown")
    p_eval.add_argument("--output", help="Optional file path to write evaluation report.")
    p_eval.set_defaults(func=_cmd_eval)

    # voice-synth (FR-5.1, FR-5.3 + Simulator Capability #6)
    p_vsynth = subparsers.add_parser(
        "voice-synth",
        help="Synthesize turn-by-turn audio with stock TTS voices, prosody normalization, and PSTN telephony DSP effects (FR-5.1, FR-5.3).",
    )
    p_vsynth.add_argument("transcript", help="Path to JSON file containing conversation turns.")
    p_vsynth.add_argument("--project", help="Customer GCP project ID for Cloud TTS API (optional with --offline).")
    p_vsynth.add_argument("--location", default="global")
    p_vsynth.add_argument("--quota-project", help="Optional X-Goog-User-Project override.")
    p_vsynth.add_argument("--output-dir", required=True, help="Output directory for WAV files and voice_manifest.json.")
    p_vsynth.add_argument(
        "--caller-voice",
        default="en-US-Journey-F",
        help=f"Approved stock TTS voice for END_USER ({sorted(ALLOWED_STOCK_VOICES)}).",
    )
    p_vsynth.add_argument(
        "--agent-voice",
        default="en-US-Journey-D",
        help="Approved stock TTS voice for HUMAN_AGENT.",
    )
    p_vsynth.add_argument(
        "--codec",
        choices=["AUDIO_ENCODING_LINEAR_16", "AUDIO_ENCODING_MULAW"],
        default="AUDIO_ENCODING_LINEAR_16",
    )
    p_vsynth.add_argument("--sample-rate-hz", type=int, default=24000)
    p_vsynth.add_argument("--chunk-ms", type=int, default=100)
    p_vsynth.add_argument("--cadence-ms", type=int, default=100)
    p_vsynth.add_argument("--jitter-ms", type=int, default=0)
    p_vsynth.add_argument("--single-utterance", action="store_true")
    p_vsynth.add_argument(
        "--telephone-filter",
        action="store_true",
        help="Apply G.711 PSTN bandpass filter (190 Hz - 3100 Hz) and soft-knee telephony saturation.",
    )
    p_vsynth.add_argument(
        "--noise-profile",
        choices=list(ALLOWED_NOISE_PROFILES),
        default="none",
        help="Mix ambient background noise (none, call_center, white_noise).",
    )
    p_vsynth.add_argument(
        "--stereo",
        action="store_true",
        help="Also export a dual-channel full_call_stereo.wav (Caller=Left, Agent=Right).",
    )
    p_vsynth.add_argument(
        "--include-transfer-ring",
        action="store_true",
        help="Prepend a 440Hz+480Hz North American PSTN transfer ringback tone.",
    )
    p_vsynth.add_argument(
        "--offline",
        action="store_true",
        help="Generate speech-cadence harmonic test WAVs locally without calling Cloud TTS API.",
    )
    p_vsynth.set_defaults(func=_cmd_voice_synth)

    # voice-replay (FR-5.2, FR-5.3)
    p_vreplay = subparsers.add_parser(
        "voice-replay",
        help="Replay synthesized voice_manifest.json audio turns in customer GCP project (FR-5.2, FR-5.3).",
    )
    p_vreplay.add_argument("manifest_dir", help="Directory containing voice_manifest.json and WAV turns.")
    p_vreplay.add_argument("--profile", required=True, help="Full ConversationProfile resource path.")
    p_vreplay.add_argument("--project", help="Optional GCP project ID override.")
    p_vreplay.add_argument("--location", help="Optional GCP location override.")
    p_vreplay.add_argument("--quota-project", help="Optional X-Goog-User-Project override.")
    p_vreplay.add_argument("--env", choices=["prod", "staging"], default="prod")
    p_vreplay.add_argument("--simulate-pacing", action="store_true", help="Simulate chunk cadence/jitter sleep.")
    p_vreplay.add_argument("--output", help="Optional file path to write replay results JSON.")
    p_vreplay.set_defaults(func=_cmd_voice_replay)

    # latency-report (FR-6.1 - FR-6.4)
    p_lat = subparsers.add_parser(
        "latency-report",
        help="Generate per-call and aggregate latency breakdown report across the 6 canonical call spans (FR-6.1 – FR-6.4).",
    )
    p_lat.add_argument("dataset", help="Path to evaluation/telemetry JSON dataset containing latency_spans_ms.")
    p_lat.add_argument("--format", choices=["markdown", "json", "html"], default="markdown")
    p_lat.add_argument("--output", help="Optional file path to write latency report or HTML dashboard.")
    p_lat.set_defaults(func=_cmd_latency_report)

    # hillclimb (FR-7.1 - FR-7.3)
    p_hc = subparsers.add_parser(
        "hillclimb",
        help="Estimate Vertex AI cost, analyze evaluation loss patterns, and propose candidate bundle fixes (FR-7.1 – FR-7.3).",
    )
    p_hc.add_argument("--bundle", required=True, help="Path to local exported configuration bundle.")
    p_hc.add_argument("--dataset", required=True, help="Path to turn-by-turn evaluation JSON dataset.")
    p_hc.add_argument("--iterations", type=int, default=1, help="Number of hill-climbing iterations to estimate.")
    p_hc.add_argument(
        "--estimate-only",
        action="store_true",
        help="Calculate and display pre-run Vertex AI token/USD cost estimate without running evaluation (FR-7.2).",
    )
    p_hc.add_argument(
        "--candidate-output",
        help="Optional directory or .zip path to write the proposed candidate bundle (FR-7.3).",
    )
    p_hc.add_argument("--format", choices=["markdown", "json"], default="markdown")
    p_hc.add_argument("--output", help="Optional file path to write hill-climbing report.")
    p_hc.set_defaults(func=_cmd_hillclimb)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
