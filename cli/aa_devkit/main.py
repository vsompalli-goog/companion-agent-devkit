"""Stateless CLI entrypoint (`aa-devkit`) for Companion Agent DevKit (L1 & L2)."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from aa_devkit.client import CompanionAgentRestClient, parse_resource_path
from aa_devkit.exporter import load_export_bundle, write_export_bundle
from aa_devkit.pack_builder import install_pack
from aa_devkit.reviewer import (
    format_review_json,
    format_review_markdown,
    review_bundle,
)


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

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(output_text)
        print(f"Wrote review report to {args.output}")
    else:
        sys.stdout.write(output_text)

    if args.fail_on_p0 and any(f.severity == "P0" for f in findings):
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aa-devkit",
        description="Stateless CLI for Agent Assist Companion Agent DevKit (L1 skills pack & L2 config export/review).",
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
    p_export.add_argument(
        "--project",
        help="Optional GCP project ID override (inferred from --profile/--agent by default).",
    )
    p_export.add_argument(
        "--location",
        help="Optional GCP location override (inferred from --profile/--agent by default).",
    )
    p_export.add_argument(
        "--quota-project",
        help="Optional X-Goog-User-Project billing/quota project override.",
    )
    p_export.add_argument(
        "--env",
        choices=["prod", "staging"],
        default="prod",
        help="Target Dialogflow/CES environment ('prod' or 'staging').",
    )
    p_export.add_argument(
        "--skip-tools",
        action="store_true",
        help="Skip fetching individual CES/Dialogflow tool definitions.",
    )
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
    p_review.add_argument(
        "bundle",
        help="Path to local exported configuration folder or .zip archive.",
    )
    p_review.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="Output report format (default: markdown).",
    )
    p_review.add_argument(
        "--output",
        help="Optional file path to write the review report.",
    )
    p_review.add_argument(
        "--fail-on-p0",
        action="store_true",
        help="Return exit code 1 if any P0 critical blocker is found.",
    )
    p_review.set_defaults(func=_cmd_review)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
