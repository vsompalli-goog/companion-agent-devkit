"""Stateless CLI entrypoint (`aa-devkit`) for Companion Agent DevKit (L1 – L4)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Optional

from aa_devkit.client import CompanionAgentRestClient, parse_resource_path
from aa_devkit.deployer import (
    apply_local_bundle,
    compute_bundle_diff,
    format_diff_report,
)
from aa_devkit.eval_pipeline import evaluate_dataset, format_eval_markdown
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
from aa_devkit.skill_eval import run_skill_regression_eval
from aa_devkit.voice_tester import (
    ALLOWED_STOCK_VOICES,
    StreamingProfile,
    replay_voice_manifest,
    synthesize_transcript_audio,
)


def _infer_project_and_location_from_bundle(
    bundle: dict[str, Any],
    project_arg: Optional[str],
    location_arg: Optional[str],
) -> tuple[str, str]:
    prof_name = (bundle.get("conversation_profile") or {}).get("name") or ""
    agent_name = (bundle.get("companion_agent") or {}).get("name") or ""
    primary_ref = prof_name or agent_name
    inferred_project, inferred_loc, _ = parse_resource_path(primary_ref) if primary_ref else ("", "", "")
    project_id = project_arg or inferred_project or (bundle.get("manifest") or {}).get("projectId") or ""
    location = location_arg or inferred_loc or (bundle.get("manifest") or {}).get("location") or "global"
    return project_id, location


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


def _cmd_diff(args: argparse.Namespace) -> int:
    local_bundle = load_export_bundle(args.bundle)
    if args.target_bundle:
        target_bundle = load_export_bundle(args.target_bundle)
    else:
        project_id, location = _infer_project_and_location_from_bundle(
            local_bundle, args.project, args.location
        )
        if not project_id:
            print(
                "Error: Could not infer GCP project ID for remote diff; pass --project or --target-bundle.",
                file=sys.stderr,
            )
            return 2
        client = CompanionAgentRestClient(
            project_id=project_id,
            location=location,
            environment=args.env,
            quota_project_id=args.quota_project or project_id,
        )
        prof_name = (local_bundle.get("conversation_profile") or {}).get("name")
        agent_name = (local_bundle.get("companion_agent") or {}).get("name")
        target_bundle = client.harvest_configuration_graph(
            profile_name=prof_name,
            companion_agent_name=agent_name,
            include_tools=True,
        )

    diff_result = compute_bundle_diff(local_bundle, target_bundle)
    if args.format == "json":
        output_text = json.dumps(diff_result, indent=2) + "\n"
    else:
        output_text = format_diff_report(diff_result)
    _write_or_print(output_text, args.output)
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    local_bundle = load_export_bundle(args.bundle)

    # 1. Enforce deterministic P0 validation before deployment (FR-3.1)
    findings = review_bundle(local_bundle)
    p0_findings = [f for f in findings if f.severity == "P0"]
    if p0_findings and not args.skip_p0_gate:
        print(
            f"Error: Blocked deployment due to {len(p0_findings)} P0 critical configuration finding(s):",
            file=sys.stderr,
        )
        for f in p0_findings:
            print(f"  - [{f.rule_id}] {f.title} ({f.location})", file=sys.stderr)
        return 1

    # 2. Enforce explicit human confirmation (FR-3.3)
    if not args.confirm:
        print(
            "Error: Stateless deployment requires explicit human approval via --confirm "
            "and a change reference via --change-ticket (FR-3.3, FR-4.4).",
            file=sys.stderr,
        )
        return 2

    project_id, location = _infer_project_and_location_from_bundle(
        local_bundle, args.project, args.location
    )
    if not project_id:
        print(
            "Error: Could not infer GCP project ID from local bundle; pass --project explicitly.",
            file=sys.stderr,
        )
        return 2

    client = CompanionAgentRestClient(
        project_id=project_id,
        location=location,
        environment=args.env,
        quota_project_id=args.quota_project or project_id,
        request_reason=args.change_ticket,
    )
    result = apply_local_bundle(
        client=client,
        local_bundle=local_bundle,
        change_ticket=args.change_ticket,
    )
    print(
        f"Applied {result['applied_count']} resource(s) to project '{project_id}' "
        f"(audit ticket: {result['change_ticket']}):"
    )
    for res in result["applied_resources"]:
        print(f"  - [{res['action']}] {res['type']}: {res['name']}")
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

    report = evaluate_dataset(
        dataset_path=args.dataset,
        client=client,
        profile_name=args.profile,
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
    )
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
    )
    print(
        f"Synthesized {len(manifest['turns'])} turn(s) using stock voices only "
        f"(derivedFromCustomerData={manifest['derivedFromCustomerData']}) into {args.output_dir}"
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

    eval_report = evaluate_dataset(dataset_path=args.dataset)
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
        description="Stateless CLI for Agent Assist Companion Agent DevKit (L1 – L4).",
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

    # diff (FR-3.2)
    p_diff = subparsers.add_parser(
        "diff",
        help="Stateless dry-run diff between a local bundle and a target bundle or live GCP project (FR-3.2).",
    )
    p_diff.add_argument("bundle", help="Path to candidate local bundle folder or .zip.")
    p_diff.add_argument(
        "--target-bundle",
        help="Optional baseline local bundle folder or .zip (if omitted, diffs against live GCP project).",
    )
    p_diff.add_argument("--project", help="Optional GCP project ID override for live remote diff.")
    p_diff.add_argument("--location", help="Optional GCP location override.")
    p_diff.add_argument("--quota-project", help="Optional X-Goog-User-Project override.")
    p_diff.add_argument("--env", choices=["prod", "staging"], default="prod")
    p_diff.add_argument("--format", choices=["text", "json"], default="text")
    p_diff.add_argument("--output", help="Optional file path to write diff output.")
    p_diff.set_defaults(func=_cmd_diff)

    # apply (FR-3.1, FR-3.2, FR-3.3, FR-4.4)
    p_apply = subparsers.add_parser(
        "apply",
        help="Stateless audited deployment of local bundle to Dialogflow/CES with human approval gate (FR-3.1, FR-3.3, FR-4.4).",
    )
    p_apply.add_argument("bundle", help="Path to local configuration bundle folder or .zip.")
    p_apply.add_argument(
        "--change-ticket",
        required=True,
        help="Change ticket / instruction reference attached to Cloud Audit Logs via X-Goog-Request-Reason (FR-4.4).",
    )
    p_apply.add_argument(
        "--confirm",
        action="store_true",
        help="Mandatory human confirmation flag required to execute REST write calls (FR-3.3).",
    )
    p_apply.add_argument("--skip-p0-gate", action="store_true", help="Bypass P0 config validation blocker.")
    p_apply.add_argument("--project", help="Optional GCP project ID override.")
    p_apply.add_argument("--location", help="Optional GCP location override.")
    p_apply.add_argument("--quota-project", help="Optional X-Goog-User-Project override.")
    p_apply.add_argument("--env", choices=["prod", "staging"], default="prod")
    p_apply.set_defaults(func=_cmd_apply)

    # eval (FR-4.1, FR-4.2)
    p_eval = subparsers.add_parser(
        "eval",
        help="Run turn-by-turn Companion Agent evaluation pipeline offline or live via :analyzeContent (FR-4.1, FR-4.2).",
    )
    p_eval.add_argument("dataset", help="Path to turn-by-turn evaluation JSON dataset.")
    p_eval.add_argument("--live", action="store_true", help="Execute live :analyzeContent calls against GCP.")
    p_eval.add_argument("--profile", help="Full ConversationProfile resource path (required with --live).")
    p_eval.add_argument("--project", help="Optional GCP project ID override.")
    p_eval.add_argument("--location", help="Optional GCP location override.")
    p_eval.add_argument("--quota-project", help="Optional X-Goog-User-Project override.")
    p_eval.add_argument("--env", choices=["prod", "staging"], default="prod")
    p_eval.add_argument("--format", choices=["markdown", "json"], default="markdown")
    p_eval.add_argument("--output", help="Optional file path to write evaluation report.")
    p_eval.set_defaults(func=_cmd_eval)

    # voice-synth (FR-5.1, FR-5.3)
    p_vsynth = subparsers.add_parser(
        "voice-synth",
        help="Synthesize turn-by-turn audio from transcript using stock TTS voices only (FR-5.1, FR-5.3).",
    )
    p_vsynth.add_argument("transcript", help="Path to JSON file containing conversation turns.")
    p_vsynth.add_argument("--project", required=True, help="Customer GCP project ID for Cloud TTS API.")
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
