"""Stateless dry-run diff & configuration deployment engine (Section 7.4: FR-3.1, FR-3.2, FR-3.3, FR-4.4).

Strictly stateless by design:
  - Computes unified diffs between a local configuration bundle (folder or `.zip`) and
    either live GCP resources or another local bundle (`FR-3.2`).
  - Applies local JSON configurations to Dialogflow v2beta1 & CES v1beta (`FR-3.1`) only
    after deterministic P0 validation, explicit human confirmation (`FR-3.3`), and with
    Cloud Audit Logging request-reason tagging (`X-Goog-Request-Reason`, `FR-4.4`).
  - Git repository versioning, branch approvals, and rollbacks remain external to this CLI.
"""

from __future__ import annotations

import copy
import difflib
import json
from typing import Any, Optional

from aa_devkit.client import CompanionAgentRestClient, parse_resource_path

SERVER_ONLY_KEYS = {
    "createTime",
    "updateTime",
    "etag",
    "projectNumber",
    "_apiEndpoint",
    "_fetchError",
    "shortName",
    "toolSource",
    "toolType",
    "usedIn",
    "fetchError",
    "openApiSchema",
    "restEndpoint",
    "pythonCode",
    "confirmationRequirement",
    "proactiveEnabled",
    "reactiveEnabled",
}


def clean_resource_for_write(resource: dict[str, Any], drop_name: bool = False) -> dict[str, Any]:
    """Removes server-assigned and local normalization metadata before diffing or writing."""
    if not isinstance(resource, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for k, v in resource.items():
        if k in SERVER_ONLY_KEYS or str(k).startswith("_"):
            continue
        if drop_name and k == "name":
            continue
        cleaned[k] = copy.deepcopy(v)
    return cleaned


def _to_canonical_lines(resource: dict[str, Any]) -> list[str]:
    cleaned = clean_resource_for_write(resource)
    return (json.dumps(cleaned, indent=2, sort_keys=True) + "\n").splitlines(keepends=True)


def compute_bundle_diff(
    local_bundle: dict[str, Any],
    target_bundle: dict[str, Any],
) -> dict[str, Any]:
    """Computes unified diffs per resource between a local bundle and a target (remote/baseline) bundle."""
    diffs: list[dict[str, str]] = []

    def _diff_resource(label: str, local_obj: dict[str, Any], remote_obj: dict[str, Any]) -> None:
        if not local_obj and not remote_obj:
            return
        local_lines = _to_canonical_lines(local_obj) if local_obj else []
        remote_lines = _to_canonical_lines(remote_obj) if remote_obj else []
        if local_lines == remote_lines:
            return
        action = "CREATE" if not remote_obj else ("DELETE" if not local_obj else "UPDATE")
        unified = "".join(
            difflib.unified_diff(
                remote_lines,
                local_lines,
                fromfile=f"remote/{label}",
                tofile=f"local/{label}",
            )
        )
        diffs.append({"resource": label, "action": action, "diff": unified})

    local_prof = local_bundle.get("conversation_profile") or {}
    remote_prof = target_bundle.get("conversation_profile") or {}
    if local_prof:
        _diff_resource(
            local_prof.get("name") or "conversation_profile.json",
            local_prof,
            remote_prof,
        )

    local_agent = local_bundle.get("companion_agent") or {}
    remote_agent = target_bundle.get("companion_agent") or {}
    if local_agent:
        _diff_resource(
            local_agent.get("name") or "companion_agent.json",
            local_agent,
            remote_agent,
        )

    remote_tools_by_name = {
        (t.get("name") or t.get("displayName") or ""): t
        for t in (target_bundle.get("tools") or [])
        if isinstance(t, dict)
    }
    for local_tool in local_bundle.get("tools") or []:
        if not isinstance(local_tool, dict):
            continue
        t_key = local_tool.get("name") or local_tool.get("displayName") or "tool"
        remote_tool = remote_tools_by_name.get(t_key) or {}
        _diff_resource(t_key, local_tool, remote_tool)

    return {
        "has_changes": len(diffs) > 0,
        "changed_count": len(diffs),
        "changes": diffs,
    }


def format_diff_report(diff_result: dict[str, Any]) -> str:
    """Formats a bundle diff result into human-readable output."""
    if not diff_result.get("has_changes"):
        return "No configuration differences detected between local bundle and target.\n"

    lines = [
        f"Proposed Configuration Changes ({diff_result['changed_count']} resource(s)):",
        "=" * 72,
    ]
    for change in diff_result.get("changes") or []:
        lines.append(f"\n[{change['action']}] {change['resource']}")
        lines.append(change["diff"].rstrip("\n"))
    lines.append("")
    return "\n".join(lines)


def apply_local_bundle(
    client: CompanionAgentRestClient,
    local_bundle: dict[str, Any],
    change_ticket: str,
) -> dict[str, Any]:
    """Applies local bundle resources (Tools, CompanionAgent, ConversationProfile) via REST.

    Scoped strictly to the named resources in `local_bundle` and attaches
    `X-Goog-Request-Reason: <change_ticket>` for Cloud Audit Logging (`FR-4.4`).
    """
    if not change_ticket or not change_ticket.strip():
        raise ValueError(
            "A non-empty --change-ticket / instruction reference is required for Cloud Audit Logging (FR-4.4)."
        )

    audit_reason = change_ticket.strip()
    applied_resources: list[dict[str, str]] = []

    # 1. Apply CES / Dialogflow Tools first so CompanionAgent tool links resolve
    ces_base = client._get_ces_base_urls()[0]
    for tool in local_bundle.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        raw_name = str(tool.get("name") or "").strip()
        if not raw_name:
            continue
        _, _, clean_path = parse_resource_path(raw_name)

        if "/apps/" in clean_path:
            ces_body: dict[str, Any] = {
                "displayName": tool.get("displayName") or clean_path.split("/")[-1],
                "executionType": tool.get("executionType", "SYNCHRONOUS"),
            }
            if tool.get("description"):
                ces_body["description"] = tool["description"]
            py_fn = dict(tool.get("pythonFunction") or {})
            if tool.get("pythonCode") and not py_fn.get("pythonCode"):
                py_fn["pythonCode"] = tool["pythonCode"]
            if py_fn:
                ces_body["pythonFunction"] = py_fn
            elif tool.get("openApiTool"):
                ces_body["openApiTool"] = tool["openApiTool"]

            mask = ",".join(
                k
                for k in ("displayName", "description", "executionType", "pythonFunction", "openApiTool")
                if k in ces_body
            )
            try:
                client.request(
                    "GET",
                    f"{ces_base}/{clean_path}",
                    timeout=10,
                    request_reason=audit_reason,
                )
                client.request(
                    "PATCH",
                    f"{ces_base}/{clean_path}",
                    json_body=ces_body,
                    params={"updateMask": mask},
                    request_reason=audit_reason,
                )
                applied_resources.append({"type": "CES_TOOL", "name": clean_path, "action": "PATCH"})
            except Exception:
                app_path, tool_id = clean_path.split("/tools/", 1)
                client.request(
                    "POST",
                    f"{ces_base}/{app_path}/tools",
                    json_body=ces_body,
                    params={"toolId": tool_id},
                    request_reason=audit_reason,
                )
                applied_resources.append({"type": "CES_TOOL", "name": clean_path, "action": "POST"})
        else:
            df_body = clean_resource_for_write(tool)
            mask = ",".join(
                k for k in ("toolKey", "displayName", "description", "functionSpec", "openApiSpec") if k in df_body
            )
            try:
                client.request("GET", f"v2beta1/{clean_path}", timeout=10, request_reason=audit_reason)
                client.request(
                    "PATCH",
                    f"v2beta1/{clean_path}",
                    json_body=df_body,
                    params={"updateMask": mask},
                    request_reason=audit_reason,
                )
                applied_resources.append({"type": "DIALOGFLOW_TOOL", "name": clean_path, "action": "PATCH"})
            except Exception:
                parent, tool_id = clean_path.split("/tools/", 1)
                df_create = clean_resource_for_write(tool, drop_name=True)
                client.request(
                    "POST",
                    f"v2beta1/{parent}/tools",
                    json_body=df_create,
                    params={"toolId": tool_id},
                    request_reason=audit_reason,
                )
                applied_resources.append({"type": "DIALOGFLOW_TOOL", "name": clean_path, "action": "POST"})

    # 2. Apply CompanionAgent
    agent = local_bundle.get("companion_agent") or {}
    if agent and agent.get("name"):
        _, _, clean_ca = parse_resource_path(agent["name"])
        agent_body = clean_resource_for_write(agent)
        agent_body["name"] = clean_ca
        mask_fields = [
            k
            for k in ("displayName", "description", "skillConfigs", "cesToolSpecs", "companionAgentWorkflowSpecs")
            if k in agent_body
        ]
        mask = ",".join(mask_fields) or "skillConfigs"
        try:
            client.request("GET", f"v2beta1/{clean_ca}", timeout=10, request_reason=audit_reason)
            client.request(
                "PATCH",
                f"v2beta1/{clean_ca}",
                json_body=agent_body,
                params={"updateMask": mask},
                request_reason=audit_reason,
            )
            applied_resources.append({"type": "COMPANION_AGENT", "name": clean_ca, "action": "PATCH"})
        except Exception:
            parent, agent_id = clean_ca.split("/companionAgents/", 1)
            create_body = clean_resource_for_write(agent, drop_name=True)
            client.request(
                "POST",
                f"v2beta1/{parent}/companionAgents",
                json_body=create_body,
                params={"companionAgentId": agent_id},
                request_reason=audit_reason,
            )
            applied_resources.append({"type": "COMPANION_AGENT", "name": clean_ca, "action": "POST"})

    # 3. Apply ConversationProfile
    profile = local_bundle.get("conversation_profile") or {}
    if profile and profile.get("name"):
        _, _, clean_cp = parse_resource_path(profile["name"])
        prof_body = clean_resource_for_write(profile)
        prof_body["name"] = clean_cp
        mask_fields = [
            k for k in ("displayName", "humanAgentAssistantConfig", "sttConfig", "automatedAgentConfig") if k in prof_body
        ]
        mask = ",".join(mask_fields) or "humanAgentAssistantConfig"
        client.request(
            "PATCH",
            f"v2beta1/{clean_cp}",
            json_body=prof_body,
            params={"updateMask": mask},
            request_reason=audit_reason,
        )
        applied_resources.append({"type": "CONVERSATION_PROFILE", "name": clean_cp, "action": "PATCH"})

    return {
        "change_ticket": audit_reason,
        "applied_count": len(applied_resources),
        "applied_resources": applied_resources,
    }
