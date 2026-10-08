"""Unit tests for stateless configuration graph exporter & bundle loader (FR-2.1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from aa_devkit.client import CompanionAgentRestClient, parse_resource_path
from aa_devkit.exporter import load_export_bundle, write_export_bundle


class _FakeRestClient(CompanionAgentRestClient):
    def __init__(self) -> None:
        self.project_id = "demo-proj"
        self.location = "global"
        self.environment = "prod"
        self.quota_project_id = "demo-proj"
        self.base_url = "https://dialogflow.googleapis.com"
        self.credentials = None
        self.session = None

    def request(
        self,
        method: str,
        path_or_url: str,
        json_body: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        if "conversationProfiles/prof-1" in path_or_url:
            return {
                "name": "projects/demo-proj/locations/global/conversationProfiles/prof-1",
                "displayName": "Demo Profile",
                "humanAgentAssistantConfig": {
                    "humanAgentSuggestionConfig": {
                        "companionAgent": "projects/demo-proj/locations/global/companionAgents/agent-1",
                        "featureConfigs": [
                            {"suggestionFeature": {"type": "CONVERSATION_SUMMARIZATION"}}
                        ],
                    }
                },
            }
        if "companionAgents/agent-1" in path_or_url:
            return {
                "name": "projects/demo-proj/locations/global/companionAgents/agent-1",
                "displayName": "Demo Agent",
                "cesToolSpecs": [
                    {
                        "cesTool": "projects/demo-proj/locations/global/apps/app-1/tools/lookup_account",
                        "confirmationRequirement": "NOT_REQUIRED",
                        "proactiveEnabled": True,
                        "reactiveEnabled": True,
                    }
                ],
                "companionAgentWorkflowSpecs": [
                    {
                        "workflow": "projects/demo-proj/locations/global/companionAgentWorkflows/wf-1",
                        "proactiveEnabled": True,
                        "reactiveEnabled": False,
                    }
                ],
            }
        if "companionAgentWorkflows/wf-1" in path_or_url:
            return {
                "name": "projects/demo-proj/locations/global/companionAgentWorkflows/wf-1",
                "displayName": "Billing Dispute",
                "description": "Applies when customer disputes a billing charge.",
                "steps": [
                    {
                        "id": "step-1",
                        "title": "Lookup Account",
                        "actions": [
                            {
                                "systemAction": {
                                    "cesToolSpec": {
                                        "cesTool": "projects/demo-proj/locations/global/apps/app-1/tools/lookup_account",
                                        "confirmationRequirement": "NOT_REQUIRED",
                                    }
                                }
                            }
                        ],
                    }
                ],
            }
        if "apps/app-1/tools/lookup_account" in path_or_url:
            return {
                "name": "projects/demo-proj/locations/global/apps/app-1/tools/lookup_account",
                "displayName": "lookup_account",
                "pythonFunction": {
                    "name": "lookup_account",
                    "pythonCode": (
                        "def lookup_account(account_id: str = '') -> dict:\n"
                        '    """Looks up account.\n\n    Args:\n        account_id: ID.\n\n    Returns:\n        Account dict.\n    """\n'
                        "    return {'id': account_id}\n"
                    ),
                },
            }
        raise AssertionError(f"Unexpected REST path: {path_or_url}")


def test_parse_resource_path() -> None:
    proj, loc, clean = parse_resource_path(
        "v2beta1/projects/my-proj/locations/us-central1/conversationProfiles/p1"
    )
    assert proj == "my-proj"
    assert loc == "us-central1"
    assert clean == "projects/my-proj/locations/us-central1/conversationProfiles/p1"


def test_harvest_and_export_folder_and_zip(tmp_path: Path) -> None:
    client = _FakeRestClient()
    graph = client.harvest_configuration_graph(
        profile_name="projects/demo-proj/locations/global/conversationProfiles/prof-1"
    )
    assert graph["manifest"]["workflowCount"] == 1
    assert graph["manifest"]["toolCount"] == 1

    # Write and load folder bundle
    folder_out = tmp_path / "bundle_dir"
    write_export_bundle(graph, folder_out)
    assert (folder_out / "manifest.json").is_file()
    assert (folder_out / "conversation_profile.json").is_file()
    assert (folder_out / "companion_agent.json").is_file()
    assert (folder_out / "workflows" / "wf-1.json").is_file()
    assert (folder_out / "tools" / "ces" / "lookup_account.json").is_file()

    loaded_from_dir = load_export_bundle(folder_out)
    assert loaded_from_dir["companion_agent"]["displayName"] == "Demo Agent"
    assert len(loaded_from_dir["workflows"]) == 1
    assert len(loaded_from_dir["tools"]) == 1

    # Write and load .zip bundle ("zip route first")
    zip_out = tmp_path / "bundle.zip"
    write_export_bundle(graph, zip_out)
    assert zip_out.is_file()

    loaded_from_zip = load_export_bundle(zip_out)
    assert loaded_from_zip["companion_agent"]["displayName"] == "Demo Agent"
    assert len(loaded_from_zip["workflows"]) == 1
    assert len(loaded_from_zip["tools"]) == 1
