"""Pure REST v2beta1 (Dialogflow) and v1beta (CES) client for Companion Agent DevKit.

Reuses proven endpoint resolution, authentication headers (`X-Goog-User-Project`),
and resource harvesting patterns so preview fields (`companionSuggestionInput`,
`companionAgents`, `companionAgentWorkflows`, `cesToolSpecs`) are preserved
without being stripped by public Python SDK protobuf definitions.
"""

from __future__ import annotations

import concurrent.futures
import datetime
import logging
import re
from typing import Any, Optional

import google.auth
from google.auth.transport.requests import AuthorizedSession
from google.auth.transport.requests import Request as GoogleAuthRequest

logger = logging.getLogger(__name__)

STAGING_ENDPOINTS = {
    "global": "https://staging-dialogflow-googleapis.sandbox.google.com",
    "us": "https://us-staging-dialogflow-googleapis.sandbox.google.com",
    "us-central1": "https://staging-qual-us-central1-dialogflow.sandbox.googleapis.com",
    "europe-west2": "https://staging-qual-europe-west2-dialogflow.sandbox.googleapis.com",
}


def get_dialogflow_base_url(location: str = "global", environment: str = "prod") -> str:
    """Returns the regional or global Dialogflow REST API base URL for 'prod' or 'staging'."""
    loc = (location or "global").strip().lower()
    env = (environment or "prod").strip().lower()

    if env == "staging":
        if loc in STAGING_ENDPOINTS:
            return STAGING_ENDPOINTS[loc]
        return f"https://staging-qual-{loc}-dialogflow.sandbox.googleapis.com"

    if loc == "global":
        return "https://dialogflow.googleapis.com"
    return f"https://{loc}-dialogflow.googleapis.com"


def parse_resource_path(resource_path: str) -> tuple[str, str, str]:
    """Extracts (project_id, location, normalized_resource_path) from a GCP resource path."""
    clean = (resource_path or "").strip().lstrip("/")
    for prefix in ("v1beta/", "v2beta1/", "v2/"):
        if clean.startswith(prefix):
            clean = clean[len(prefix) :]

    parts = clean.split("/")
    project_id = ""
    location = "global"

    if len(parts) >= 2 and parts[0] == "projects":
        project_id = parts[1]
        if len(parts) >= 4 and parts[2] == "locations":
            location = parts[3]
        elif len(parts) == 4:
            # Normalize e.g. projects/<proj>/conversationProfiles/<id> -> projects/<proj>/locations/global/...
            clean = f"projects/{parts[1]}/locations/global/{parts[2]}/{parts[3]}"

    return project_id, location, clean


class CompanionAgentRestClient:
    """Stateless REST client for exporting and inspecting Companion Agent configurations."""

    def __init__(
        self,
        project_id: str,
        location: str = "global",
        environment: str = "prod",
        credentials: Any = None,
        quota_project_id: Optional[str] = None,
    ):
        self.project_id = project_id
        self.location = (location or "global").strip().lower()
        self.environment = (environment or "prod").strip().lower()
        self.quota_project_id = quota_project_id or project_id
        self.base_url = get_dialogflow_base_url(self.location, self.environment)

        if credentials is None:
            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        self.credentials = credentials
        self.session = AuthorizedSession(self.credentials)

    def _refresh_if_needed(self) -> None:
        if (
            self.credentials
            and getattr(self.credentials, "expired", False)
            and getattr(self.credentials, "refresh_token", None)
        ):
            self.credentials.refresh(GoogleAuthRequest())
            self.session = AuthorizedSession(self.credentials)

    def _get_ces_base_urls(self) -> list[str]:
        if self.environment == "staging":
            return [
                "https://staging-ces.sandbox.googleapis.com/v1beta",
                "https://ces.googleapis.com/v1beta",
            ]
        return [
            "https://ces.googleapis.com/v1beta",
            "https://staging-ces.sandbox.googleapis.com/v1beta",
        ]

    def request(
        self,
        method: str,
        path_or_url: str,
        json_body: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
        timeout: int = 30,
        request_reason: Optional[str] = None,
    ) -> dict[str, Any]:
        """Executes an authenticated REST request against Dialogflow v2beta1 or CES v1beta."""
        self._refresh_if_needed()
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            url = path_or_url
        else:
            url = f"{self.base_url}/{path_or_url.lstrip('/')}"

        headers = {
            "Content-Type": "application/json",
            "X-Goog-User-Project": self.quota_project_id,
        }
        if request_reason:
            headers["X-Goog-Request-Reason"] = str(request_reason).strip()

        response = self.session.request(
            method=method,
            url=url,
            headers=headers,
            json=json_body,
            params=params,
            timeout=(5, timeout),
        )
        if not response.ok:
            error_detail = response.text
            try:
                err_json = response.json()
                error_detail = err_json.get("error", {}).get("message", response.text)
            except Exception:
                pass
            raise RuntimeError(
                f"Dialogflow/CES {self.environment.upper()} API Error ({response.status_code}) on {url}: {error_detail}"
            )
        if not response.text:
            return {}
        return response.json()

    @staticmethod
    def extract_companion_agent_from_profile(profile_data: dict[str, Any]) -> str:
        """Extracts the linked CompanionAgent resource path from a ConversationProfile."""
        if not isinstance(profile_data, dict):
            return ""
        haa_cfg = profile_data.get("humanAgentAssistantConfig") or {}
        sugg_cfg = haa_cfg.get("humanAgentSuggestionConfig") or {}
        ca_name = sugg_cfg.get("companionAgent")
        if ca_name:
            return str(ca_name)
        for fc in sugg_cfg.get("featureConfigs") or []:
            if not isinstance(fc, dict):
                continue
            ca_cfg = fc.get("companionAgentConfig") or {}
            if ca_cfg.get("agent"):
                return str(ca_cfg["agent"])
            if fc.get("companionAgent"):
                return str(fc["companionAgent"])
        return ""

    def get_conversation_profile(self, profile_name: str) -> dict[str, Any]:
        _, _, clean_name = parse_resource_path(profile_name)
        return self.request("GET", f"v2beta1/{clean_name}")

    def get_companion_agent(self, companion_agent_name: str) -> dict[str, Any]:
        _, _, clean_name = parse_resource_path(companion_agent_name)
        return self.request("GET", f"v2beta1/{clean_name}")

    def get_companion_agent_workflow(self, workflow_name: str) -> dict[str, Any]:
        _, _, clean_name = parse_resource_path(workflow_name)
        return self.request("GET", f"v2beta1/{clean_name}")

    @staticmethod
    def normalize_tool_definition(
        tool_raw: dict[str, Any],
        tool_path: str = "",
        usage_meta: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Normalizes a CES v1beta or Dialogflow v2beta1 Tool into a rich inspectable dict."""
        if not isinstance(tool_raw, dict):
            tool_raw = {}
        resolved_path = tool_raw.get("name") or tool_path or ""
        short_id = (
            resolved_path.split("/")[-1]
            if resolved_path
            else (tool_raw.get("toolKey") or tool_raw.get("displayName") or "tool")
        )
        is_ces = (
            "/apps/" in resolved_path
            or "pythonFunction" in tool_raw
            or "openApiTool" in tool_raw
        )
        tool_source = "CES" if is_ces else "DIALOGFLOW"

        py_fn = tool_raw.get("pythonFunction") or {}
        open_api = tool_raw.get("openApiSpec") or tool_raw.get("openApiTool") or {}
        fn_spec = tool_raw.get("functionSpec") or tool_raw.get("clientFunction") or {}
        conn_spec = tool_raw.get("connectorSpec") or tool_raw.get("connectorTool") or {}
        ext_spec = tool_raw.get("extensionSpec") or {}

        python_code = (
            py_fn.get("pythonCode")
            or py_fn.get("code")
            or tool_raw.get("pythonCode")
            or ""
        )
        openapi_schema = (
            open_api.get("textSchema")
            or open_api.get("openApiSchema")
            or (open_api if isinstance(open_api, str) else "")
        )
        rest_endpoint = (
            open_api.get("url")
            or open_api.get("endpoint")
            or tool_raw.get("restEndpoint")
            or ""
        )
        if not rest_endpoint and isinstance(openapi_schema, str) and openapi_schema.strip():
            url_match = re.search(r"https?://[^\s\"'\)]+", openapi_schema)
            if url_match:
                rest_endpoint = url_match.group(0)

        if python_code or py_fn:
            tool_type = "PYTHON_FUNCTION"
        elif openapi_schema or open_api:
            tool_type = "OPENAPI_REST"
        elif fn_spec:
            tool_type = "FUNCTION_SPEC"
        elif conn_spec:
            tool_type = "CONNECTOR"
        elif ext_spec:
            tool_type = "EXTENSION"
        else:
            tool_type = tool_raw.get("executionType") or "TOOL"

        display_name = (
            tool_raw.get("displayName")
            or tool_raw.get("toolKey")
            or py_fn.get("name")
            or short_id
        )
        description = (
            tool_raw.get("description")
            or py_fn.get("description")
            or (open_api.get("description") if isinstance(open_api, dict) else "")
            or fn_spec.get("description")
            or ""
        )

        usage_meta = usage_meta or {}
        return {
            **tool_raw,
            "name": resolved_path,
            "shortName": short_id,
            "displayName": display_name,
            "description": description,
            "toolSource": tool_source,
            "toolType": tool_type,
            "executionType": tool_raw.get("executionType", "SYNCHRONOUS"),
            "pythonCode": python_code,
            "openApiSchema": openapi_schema,
            "restEndpoint": rest_endpoint,
            "confirmationRequirement": usage_meta.get("confirmationRequirement", "UNSPECIFIED"),
            "proactiveEnabled": usage_meta.get("proactiveEnabled"),
            "reactiveEnabled": usage_meta.get("reactiveEnabled"),
            "usedIn": usage_meta.get("usedIn", []),
            "fetchError": tool_raw.get("_fetchError"),
        }

    def get_tool_details(
        self, tool_path: str, usage_meta: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """Fetches a CES v1beta Tool (`.../apps/.../tools/...`) or Dialogflow v2beta1 Tool."""
        _, _, clean_path = parse_resource_path(tool_path)
        if not clean_path:
            return self.normalize_tool_definition({}, "", usage_meta)

        last_err: Optional[str] = None
        if "/apps/" in clean_path:
            for ces_base in self._get_ces_base_urls():
                try:
                    raw_tool = self.request("GET", f"{ces_base}/{clean_path}", timeout=15)
                    if isinstance(raw_tool, dict) and raw_tool:
                        raw_tool["_apiEndpoint"] = ces_base
                        return self.normalize_tool_definition(raw_tool, clean_path, usage_meta)
                except Exception as exc:
                    last_err = str(exc)

            # Last resort fallback to Dialogflow v2beta1
            try:
                raw_tool = self.request("GET", f"v2beta1/{clean_path}", timeout=12)
                if isinstance(raw_tool, dict) and raw_tool:
                    raw_tool["_apiEndpoint"] = f"{self.base_url}/v2beta1"
                    return self.normalize_tool_definition(raw_tool, clean_path, usage_meta)
            except Exception as exc:
                last_err = last_err or str(exc)
        else:
            try:
                raw_tool = self.request("GET", f"v2beta1/{clean_path}", timeout=15)
                if isinstance(raw_tool, dict) and raw_tool:
                    raw_tool["_apiEndpoint"] = f"{self.base_url}/v2beta1"
                    return self.normalize_tool_definition(raw_tool, clean_path, usage_meta)
            except Exception as exc:
                last_err = str(exc)

        logger.warning("Could not fetch full tool definition for %s: %s", clean_path, last_err)
        return self.normalize_tool_definition(
            {"name": clean_path, "_fetchError": last_err},
            clean_path,
            usage_meta,
        )

    def harvest_configuration_graph(
        self,
        profile_name: Optional[str] = None,
        companion_agent_name: Optional[str] = None,
        include_tools: bool = True,
    ) -> dict[str, Any]:
        """Harvests the full configuration graph (Profile, CompanionAgent, Workflows, Tools)."""
        profile_data: dict[str, Any] = {}
        resolved_agent_name = companion_agent_name or ""

        if profile_name:
            profile_data = self.get_conversation_profile(profile_name)
            if not resolved_agent_name:
                resolved_agent_name = self.extract_companion_agent_from_profile(profile_data)

        agent_data: dict[str, Any] = {}
        if resolved_agent_name:
            agent_data = self.get_companion_agent(resolved_agent_name)

        tool_usage_map: dict[str, dict[str, Any]] = {}

        def _register_tool_ref(
            tool_path: str,
            used_in_label: str,
            confirmation: Optional[str] = None,
            proactive: Optional[bool] = None,
            reactive: Optional[bool] = None,
        ) -> None:
            if not tool_path or not isinstance(tool_path, str):
                return
            _, _, clean_tp = parse_resource_path(tool_path)
            entry = tool_usage_map.setdefault(
                clean_tp,
                {
                    "confirmationRequirement": confirmation or "UNSPECIFIED",
                    "proactiveEnabled": proactive,
                    "reactiveEnabled": reactive,
                    "usedIn": [],
                },
            )
            if confirmation and entry["confirmationRequirement"] == "UNSPECIFIED":
                entry["confirmationRequirement"] = confirmation
            if proactive is not None and entry["proactiveEnabled"] is None:
                entry["proactiveEnabled"] = proactive
            if reactive is not None and entry["reactiveEnabled"] is None:
                entry["reactiveEnabled"] = reactive
            if used_in_label and used_in_label not in entry["usedIn"]:
                entry["usedIn"].append(used_in_label)

        for t_spec in agent_data.get("cesToolSpecs") or []:
            if isinstance(t_spec, dict) and t_spec.get("cesTool"):
                conf = t_spec.get("confirmationRequirement", "NOT_REQUIRED")
                pro = t_spec.get("proactiveEnabled", False)
                rea = t_spec.get("reactiveEnabled", False)
                mode_label = " + ".join(
                    m for m, enabled in [("Proactive", pro), ("Reactive", rea)] if enabled
                ) or "Unbound"
                _register_tool_ref(
                    t_spec["cesTool"],
                    f"Companion Agent ({mode_label}, {conf})",
                    confirmation=conf,
                    proactive=pro,
                    reactive=rea,
                )

        for t_path in agent_data.get("tools") or []:
            if isinstance(t_path, str):
                _register_tool_ref(t_path, "Companion Agent Tool")

        workflow_specs = list(agent_data.get("companionAgentWorkflowSpecs") or [])
        workflows: list[dict[str, Any]] = []

        def _fetch_single_workflow(spec: Any) -> Optional[dict[str, Any]]:
            wf_name = (
                spec.get("workflow")
                if isinstance(spec, dict)
                else (spec if isinstance(spec, str) else "")
            )
            if not wf_name:
                return None
            pro_enabled = spec.get("proactiveEnabled", False) if isinstance(spec, dict) else False
            rea_enabled = spec.get("reactiveEnabled", False) if isinstance(spec, dict) else False
            try:
                wf_data = self.get_companion_agent_workflow(wf_name)
                wf_display = wf_data.get("displayName", wf_name.split("/")[-1])
                steps = wf_data.get("steps") or wf_data.get("workflowSteps") or []
                return {
                    **wf_data,
                    "name": wf_data.get("name") or wf_name,
                    "displayName": wf_display,
                    "proactiveEnabled": pro_enabled,
                    "reactiveEnabled": rea_enabled,
                    "steps": steps,
                }
            except Exception as exc:
                logger.warning("Could not fetch workflow %s: %s", wf_name, exc)
                return {
                    "name": wf_name,
                    "displayName": wf_name.split("/")[-1],
                    "proactiveEnabled": pro_enabled,
                    "reactiveEnabled": rea_enabled,
                    "fetchError": str(exc),
                }

        if workflow_specs:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                for wf_res in pool.map(_fetch_single_workflow, workflow_specs):
                    if not wf_res:
                        continue
                    workflows.append(wf_res)
                    wf_display = wf_res.get("displayName") or "Workflow"
                    for step in wf_res.get("steps") or []:
                        if not isinstance(step, dict):
                            continue
                        step_title = (
                            step.get("title")
                            or step.get("displayName")
                            or step.get("id")
                            or "Step"
                        )
                        for act in step.get("actions") or []:
                            if not isinstance(act, dict):
                                continue
                            sys_act = act.get("systemAction") or {}
                            ces_spec = sys_act.get("cesToolSpec") or {}
                            if ces_spec.get("cesTool"):
                                _register_tool_ref(
                                    ces_spec["cesTool"],
                                    f"Workflow '{wf_display}' -> Step '{step_title}'",
                                    confirmation=ces_spec.get("confirmationRequirement"),
                                )
                        step_act = step.get("action") or {}
                        tc = step_act.get("toolCall") if isinstance(step_act, dict) else None
                        if isinstance(tc, dict) and tc.get("tool"):
                            _register_tool_ref(
                                tc["tool"],
                                f"Workflow '{wf_display}' -> Step '{step_title}'",
                            )

        tools: list[dict[str, Any]] = []
        if include_tools and tool_usage_map:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                futures = [
                    pool.submit(self.get_tool_details, t_path, u_meta)
                    for t_path, u_meta in tool_usage_map.items()
                ]
                for fut in futures:
                    tools.append(fut.result())

        return {
            "manifest": {
                "schemaVersion": "1.0",
                "exportedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "environment": self.environment,
                "apiEndpoint": self.base_url,
                "projectId": self.project_id,
                "location": self.location,
                "conversationProfile": profile_data.get("name") or profile_name or "",
                "companionAgent": agent_data.get("name") or resolved_agent_name or "",
                "workflowCount": len(workflows),
                "toolCount": len(tools),
            },
            "conversation_profile": profile_data,
            "companion_agent": agent_data,
            "workflows": workflows,
            "tools": tools,
        }
