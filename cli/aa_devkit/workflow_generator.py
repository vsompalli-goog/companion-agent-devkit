"""CompanionAgentWorkflow Generator & DAG Normalizer (`aa-devkit generate-workflow`).

Compiles PDF SOP documents (`.pdf`), Markdown/text SOPs (`.md`, `.txt`), or
shorthand/draft JSON workflow specs (`.json`) into valid, DAG-verified Dialogflow
`v2beta1` `CompanionAgentWorkflow` resources ready for `CreateCompanionAgentWorkflow`,
`UpdateCompanionAgentWorkflow`, and `PublishCompanionAgentWorkflow` (`:publish`):
  - Supports direct `.pdf` input:
      * **Offline / Deterministic Mode**: Extracts text from PDF streams (`pdftotext` or
        pure-Python `/FlateDecode` + `Tj`/`TJ` parser) and parses numbered SOP steps into
        typed `INFORMATION_COLLECTION`, `SYSTEM_ACTION`, `MANUAL_ACTION`,
        `INFORMATIONAL_MESSAGE`, and `END_WORKFLOW` nodes.
      * **Vertex AI Multimodal Mode (`--use-vertex`)**: Sends the raw PDF (`application/pdf`)
        directly to Gemini with the strict `CompanionAgentWorkflow` schema prompt, then
        validates and normalizes the resulting DAG.
  - Enforces the 63-character `displayName` limit (`companion_agent_workflow.proto`).
  - Guarantees globally unique `snake_case` variable names across all steps and
    `outputVariableMappings`, with valid `stringType`, `numberType`, `booleanType`,
    or `enumType` oneofs.
  - Verifies that `rootStepId` reaches 100% of steps with zero cycles (strict DAG),
    prunes unreachable orphan steps, and terminates every branch path at an
    `END_WORKFLOW` node (with no `actions` or `transitions`).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Optional
import zlib

from aa_devkit.client import CompanionAgentRestClient


def normalize_workflow_variable(
    var_obj: Any,
    fallback_name: str,
    seen_var_names: set[str],
) -> dict[str, Any]:
    """Normalizes a workflow Variable declaration into Dialogflow v2beta1 Variable format.

    Ensures variable names are globally unique across all steps in the workflow.
    """
    if isinstance(var_obj, str):
        var_obj = {"name": var_obj, "description": var_obj}
    elif not isinstance(var_obj, dict):
        var_obj = {}

    raw_name = (
        str(var_obj.get("name") or var_obj.get("key") or fallback_name or "collected_value")
        .strip()
        .lstrip("$")
    )
    clean_name = re.sub(r"[^a-zA-Z0-9_]", "_", raw_name).strip("_") or "collected_value"
    if clean_name[0].isdigit():
        clean_name = f"var_{clean_name}"

    base_name = clean_name
    suffix = 2
    while clean_name in seen_var_names:
        clean_name = f"{base_name}_{suffix}"
        suffix += 1
    seen_var_names.add(clean_name)

    desc = str(
        var_obj.get("description") or var_obj.get("title") or clean_name.replace("_", " ")
    ).strip()
    out_var: dict[str, Any] = {"name": clean_name}
    if desc:
        out_var["description"] = desc

    if isinstance(var_obj.get("enumType"), dict):
        vals = [
            str(v).strip()
            for v in (var_obj["enumType"].get("values") or [])
            if str(v).strip()
        ]
        out_var["enumType"] = {"values": vals} if vals else {"values": ["YES", "NO"]}
    elif isinstance(var_obj.get("enumValues") or var_obj.get("values"), list):
        vals = [
            str(v).strip()
            for v in (var_obj.get("enumValues") or var_obj.get("values") or [])
            if str(v).strip()
        ]
        out_var["enumType"] = {"values": vals} if vals else {"values": ["YES", "NO"]}
    elif "numberType" in var_obj or str(var_obj.get("type") or "").upper() in (
        "NUMBER",
        "FLOAT",
        "INTEGER",
        "INT",
        "DOUBLE",
    ):
        out_var["numberType"] = {}
    elif "booleanType" in var_obj or str(var_obj.get("type") or "").upper() in (
        "BOOLEAN",
        "BOOL",
    ):
        out_var["booleanType"] = {}
    else:
        out_var["stringType"] = {}

    return out_var


def normalize_workflow_payload(
    wf_payload: dict[str, Any],
    tool_path_map: Optional[dict[str, str]] = None,
    default_ces_app: Optional[str] = None,
) -> dict[str, Any]:
    """Normalizes a shorthand or draft workflow dict into a DAG-verified Dialogflow v2beta1 CompanionAgentWorkflow."""
    if not isinstance(wf_payload, dict):
        raise ValueError("Workflow payload must be a JSON object.")

    tool_path_map = tool_path_map or {}
    raw_display = str(
        wf_payload.get("displayName") or wf_payload.get("title") or "Companion Agent Workflow"
    ).strip()
    # companion_agent_workflow.proto enforces a 63-character limit on displayName
    display_name = raw_display[:63].strip() or "Companion Agent Workflow"
    description = str(
        wf_payload.get("description")
        or wf_payload.get("entryCriteria")
        or wf_payload.get("triggerCondition")
        or ""
    ).strip()

    raw_steps = wf_payload.get("steps") or wf_payload.get("workflowSteps") or []
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError(f"Workflow '{display_name}' must contain a non-empty 'steps' array.")

    def _resolve_tool_ref(raw_tool: str) -> str:
        t_clean = str(raw_tool or "").strip().lstrip("/")
        for prefix in ("v1beta/", "v2beta1/", "v2/"):
            if t_clean.startswith(prefix):
                t_clean = t_clean[len(prefix) :]
        t_short = t_clean.split("/")[-1] if t_clean else ""
        if t_clean in tool_path_map:
            return tool_path_map[t_clean]
        if t_short and t_short in tool_path_map:
            return tool_path_map[t_short]
        if t_short and t_short.lower() in tool_path_map:
            return tool_path_map[t_short.lower()]
        if ("/" not in t_clean or "<" in t_clean) and t_short and default_ces_app:
            return f"{default_ces_app.strip().lstrip('/')}/tools/{t_short}"
        return t_clean

    seen_step_ids: set[str] = set()
    seen_var_names: set[str] = set()
    normalized_steps: list[dict[str, Any]] = []

    for idx, st in enumerate(raw_steps):
        if not isinstance(st, dict):
            continue

        raw_id = str(st.get("id") or st.get("stepId") or st.get("name") or f"step_{idx + 1}").strip()
        if "/" in raw_id:
            raw_id = raw_id.split("/")[-1]
        step_id = re.sub(r"[^a-zA-Z0-9_-]", "_", raw_id).strip("_") or f"step_{idx + 1}"
        base_step_id = step_id
        s_suffix = 2
        while step_id in seen_step_ids:
            step_id = f"{base_step_id}_{s_suffix}"
            s_suffix += 1
        seen_step_ids.add(step_id)

        raw_type = str(st.get("type") or st.get("stepType") or "").strip().upper()
        raw_actions = st.get("actions") if isinstance(st.get("actions"), list) else []
        first_act = (
            raw_actions[0]
            if (raw_actions and isinstance(raw_actions[0], dict))
            else (st.get("action") if isinstance(st.get("action"), dict) else {})
        )

        # Map step type to exact CompanionAgentWorkflowStep.Type enum
        if raw_type in ("END_WORKFLOW", "STEP_TYPE_END", "END", "COMPLETE", "FINISH"):
            step_type = "END_WORKFLOW"
        elif raw_type in ("SYSTEM_ACTION", "STEP_TYPE_TOOL_CALL", "TOOL_CALL", "CES_TOOL", "TOOL", "USE_TOOL"):
            step_type = "SYSTEM_ACTION"
        elif raw_type in ("MANUAL_ACTION", "STEP_TYPE_MANUAL", "MANUAL", "MANUAL_EXTERNAL_SYSTEM_ACTION"):
            step_type = "MANUAL_ACTION"
        elif raw_type in ("INFORMATIONAL_MESSAGE", "STEP_TYPE_INFORM", "INFORM", "TELL_USER", "MESSAGE"):
            step_type = "INFORMATIONAL_MESSAGE"
        elif raw_type in ("INFORMATION_COLLECTION", "STEP_TYPE_COLLECT", "COLLECT", "ASK_USER"):
            step_type = "INFORMATION_COLLECTION"
        else:
            ha = first_act.get("humanAgentAction") or {}
            if (
                first_act.get("systemAction")
                or first_act.get("toolCall")
                or st.get("cesTool")
                or st.get("tool")
                or st.get("cesToolSpec")
            ):
                step_type = "SYSTEM_ACTION"
            elif (
                ha.get("manualExternalSystemAction")
                or first_act.get("manualExternalSystemAction")
                or st.get("manualAction")
            ):
                step_type = "MANUAL_ACTION"
            elif (
                ha.get("informationCollectionAction")
                or first_act.get("informationCollectionAction")
                or first_act.get("askUser")
                or st.get("variables")
                or st.get("outputVariables")
            ):
                step_type = "INFORMATION_COLLECTION"
            elif step_id.lower() in ("end_workflow", "end", "finish_workflow"):
                step_type = "END_WORKFLOW"
            else:
                step_type = "INFORMATIONAL_MESSAGE"

        step_title = str(
            st.get("title")
            or st.get("displayName")
            or ("End Workflow" if step_type == "END_WORKFLOW" else step_id.replace("_", " ").title())
        ).strip()
        step_subtitle = str(st.get("subtitle") or st.get("description") or st.get("instruction") or "").strip()

        norm_step: dict[str, Any] = {
            "id": step_id,
            "title": step_title,
            "type": step_type,
        }
        if step_subtitle:
            norm_step["subtitle"] = step_subtitle

        if step_type == "END_WORKFLOW":
            # Proto rule: END_WORKFLOW cannot have any actions or transitions
            normalized_steps.append(norm_step)
            continue

        norm_actions: list[dict[str, Any]] = []
        ha = first_act.get("humanAgentAction") if isinstance(first_act.get("humanAgentAction"), dict) else {}

        def _extract_ui_content(
            container: dict[str, Any], fallback_container: Optional[dict[str, Any]] = None
        ) -> Optional[dict[str, Any]]:
            fallback_container = fallback_container or {}
            dd = (
                container.get("displayDetails")
                or fallback_container.get("displayDetails")
                or container.get("instructions")
                or fallback_container.get("instructions")
            )
            if isinstance(dd, dict) and dd.get("content"):
                return {
                    "content": str(dd["content"]).strip(),
                    "enableMarkdown": bool(dd.get("enableMarkdown", True)),
                }
            if isinstance(dd, str) and dd.strip():
                return {"content": dd.strip(), "enableMarkdown": True}
            return None

        def _extract_msg_suggestion(
            container: dict[str, Any], fallback_container: Optional[dict[str, Any]] = None
        ) -> Optional[dict[str, Any]]:
            fallback_container = fallback_container or {}
            msc = (
                container.get("messageSuggestionConfig")
                or fallback_container.get("messageSuggestionConfig")
                or container.get("suggestedMessage")
                or fallback_container.get("suggestedMessage")
                or container.get("prompt")
                or fallback_container.get("prompt")
                or (container.get("humanAgentAction") if isinstance(container.get("humanAgentAction"), str) else None)
                or (
                    fallback_container.get("humanAgentAction")
                    if isinstance(fallback_container.get("humanAgentAction"), str)
                    else None
                )
            )
            if isinstance(msc, dict) and msc.get("content"):
                res_msc: dict[str, Any] = {"content": str(msc["content"]).strip()}
                if "verbatim" in msc:
                    res_msc["verbatim"] = bool(msc["verbatim"])
                return res_msc
            if isinstance(msc, str) and msc.strip():
                res_msc = {"content": msc.strip()}
                if "verbatim" in container:
                    res_msc["verbatim"] = bool(container["verbatim"])
                elif "verbatim" in fallback_container:
                    res_msc["verbatim"] = bool(fallback_container["verbatim"])
                return res_msc
            return None

        if step_type == "INFORMATION_COLLECTION":
            ic = (
                ha.get("informationCollectionAction")
                or first_act.get("informationCollectionAction")
                or first_act.get("askUser")
                or {}
            )
            if not isinstance(ic, dict):
                ic = {}
            ic_out: dict[str, Any] = {}
            ui_dd = _extract_ui_content(ic, st)
            if ui_dd:
                ic_out["displayDetails"] = ui_dd
            msg_cfg = _extract_msg_suggestion(ic, st)
            if msg_cfg:
                ic_out["messageSuggestionConfig"] = msg_cfg

            raw_vars = (
                ic.get("variables")
                or ic.get("outputVariables")
                or first_act.get("variables")
                or st.get("variables")
                or st.get("outputVariables")
                or []
            )
            norm_vars: list[dict[str, Any]] = []
            if isinstance(raw_vars, list):
                for v_i, v_item in enumerate(raw_vars):
                    norm_vars.append(
                        normalize_workflow_variable(v_item, f"{step_id}_var_{v_i + 1}", seen_var_names)
                    )
            if not norm_vars:
                norm_vars.append(
                    normalize_workflow_variable(
                        {"name": f"{step_id}_value", "description": step_subtitle or step_title},
                        f"{step_id}_value",
                        seen_var_names,
                    )
                )
            ic_out["variables"] = norm_vars
            norm_actions.append({"humanAgentAction": {"informationCollectionAction": ic_out}})

        elif step_type == "INFORMATIONAL_MESSAGE":
            ir = (
                ha.get("informationalResponseAction")
                or ha.get("informationalMessageAction")
                or first_act.get("informationalResponseAction")
                or first_act.get("informationalMessageAction")
                or first_act.get("tellUser")
                or {}
            )
            if not isinstance(ir, dict):
                ir = {}
            ir_out: dict[str, Any] = {}
            ui_dd = _extract_ui_content(ir, st)
            msg_cfg = _extract_msg_suggestion(ir, st)
            if not ui_dd and not msg_cfg:
                msg_cfg = {"content": step_subtitle or step_title}
            if ui_dd:
                ir_out["displayDetails"] = ui_dd
            if msg_cfg:
                ir_out["messageSuggestionConfig"] = msg_cfg
            norm_actions.append({"humanAgentAction": {"informationalResponseAction": ir_out}})

        elif step_type == "MANUAL_ACTION":
            ma = (
                ha.get("manualExternalSystemAction")
                or first_act.get("manualExternalSystemAction")
                or first_act.get("manualAction")
                or st.get("manualAction")
                or {}
            )
            if not isinstance(ma, dict):
                ma = {"displayDetails": {"content": str(ma), "enableMarkdown": True}}
            ma_out: dict[str, Any] = {}
            ui_dd = _extract_ui_content(ma, st)
            if not ui_dd:
                ui_dd = {
                    "content": step_subtitle or f"Complete manual action: {step_title}",
                    "enableMarkdown": True,
                }
            ma_out["displayDetails"] = ui_dd

            raw_vars = ma.get("variables") or st.get("variables") or []
            if isinstance(raw_vars, list) and raw_vars:
                ma_out["variables"] = [
                    normalize_workflow_variable(v_item, f"{step_id}_var_{v_i + 1}", seen_var_names)
                    for v_i, v_item in enumerate(raw_vars)
                ]
            norm_actions.append({"humanAgentAction": {"manualExternalSystemAction": ma_out}})

        elif step_type == "SYSTEM_ACTION":
            sa = first_act.get("systemAction") if isinstance(first_act.get("systemAction"), dict) else {}
            tc = first_act.get("toolCall") if isinstance(first_act.get("toolCall"), dict) else {}
            raw_ces_spec = (
                sa.get("cesToolSpec")
                or first_act.get("cesToolSpec")
                or st.get("cesToolSpec")
                or {}
            )
            if not isinstance(raw_ces_spec, dict):
                raw_ces_spec = {}
            raw_tool_name = (
                raw_ces_spec.get("cesTool")
                or raw_ces_spec.get("tool")
                or sa.get("cesTool")
                or tc.get("tool")
                or tc.get("cesTool")
                or st.get("cesTool")
                or st.get("tool")
                or ""
            )
            resolved_tool = _resolve_tool_ref(raw_tool_name)
            if not resolved_tool and tool_path_map:
                resolved_tool = next(iter(tool_path_map.values()), "")

            sa_out: dict[str, Any] = {
                "cesToolSpec": {
                    "cesTool": resolved_tool,
                    "confirmationRequirement": (
                        raw_ces_spec.get("confirmationRequirement")
                        or st.get("confirmationRequirement")
                        or "NOT_REQUIRED"
                    ),
                    "proactiveEnabled": bool(raw_ces_spec.get("proactiveEnabled", True)),
                    "reactiveEnabled": bool(raw_ces_spec.get("reactiveEnabled", True)),
                }
            }
            ui_dd = _extract_ui_content(sa, st)
            if ui_dd:
                sa_out["displayDetails"] = ui_dd

            raw_mappings = (
                sa.get("outputVariableMappings")
                or first_act.get("outputVariableMappings")
                or tc.get("outputVariableMappings")
                or st.get("outputVariableMappings")
                or []
            )
            norm_mappings: list[dict[str, Any]] = []
            if isinstance(raw_mappings, list):
                for m_i, m_item in enumerate(raw_mappings):
                    if not isinstance(m_item, dict):
                        continue
                    var_part = m_item.get("variable") if isinstance(m_item.get("variable"), dict) else m_item
                    norm_var = normalize_workflow_variable(
                        var_part, f"{step_id}_out_{m_i + 1}", seen_var_names
                    )
                    mapping_entry: dict[str, Any] = {"variable": norm_var}
                    raw_fps = (
                        m_item.get("fieldPaths")
                        or ([m_item["fieldPath"]] if m_item.get("fieldPath") else [])
                        or ([m_item["sourceField"]] if m_item.get("sourceField") else [])
                        or ([m_item["jsonPath"].lstrip("$.")] if m_item.get("jsonPath") else [])
                    )
                    fps_out: list[dict[str, str]] = []
                    for fp in raw_fps:
                        if isinstance(fp, dict) and fp.get("path"):
                            fps_out.append({"path": str(fp["path"]).strip()})
                        elif isinstance(fp, str) and fp.strip():
                            fps_out.append({"path": fp.strip()})
                    if fps_out:
                        mapping_entry["fieldPaths"] = fps_out
                    norm_mappings.append(mapping_entry)
            if norm_mappings:
                sa_out["outputVariableMappings"] = norm_mappings
            norm_actions.append({"systemAction": sa_out})

        norm_step["actions"] = norm_actions

        # Normalize transitions
        raw_transitions = (
            st.get("transitions")
            if isinstance(st.get("transitions"), list)
            else (st.get("nextSteps") if isinstance(st.get("nextSteps"), list) else [])
        )
        norm_transitions: list[dict[str, Any]] = []
        for tr in raw_transitions:
            if isinstance(tr, str) and tr.strip():
                norm_transitions.append(
                    {"targetStepId": re.sub(r"[^a-zA-Z0-9_-]", "_", tr.strip()).strip("_")}
                )
            elif isinstance(tr, dict):
                t_target = str(
                    tr.get("targetStepId") or tr.get("nextStepId") or tr.get("target") or ""
                ).strip()
                if not t_target:
                    continue
                t_target = re.sub(r"[^a-zA-Z0-9_-]", "_", t_target).strip("_")
                tr_entry: dict[str, Any] = {"targetStepId": t_target}
                cond = tr.get("condition")
                if isinstance(cond, dict) and cond.get("variableCondition"):
                    tr_entry["condition"] = {
                        "variableCondition": str(cond["variableCondition"]).strip()
                    }
                elif (
                    isinstance(cond, str)
                    and cond.strip()
                    and cond.strip().upper() not in ("ALWAYS", "TRUE", "DEFAULT", "NONE")
                ):
                    tr_entry["condition"] = {"variableCondition": cond.strip()}
                norm_transitions.append(tr_entry)

        norm_step["transitions"] = norm_transitions
        normalized_steps.append(norm_step)

    if not normalized_steps:
        raise ValueError(f"Workflow '{display_name}' has no valid steps after normalization.")

    # Ensure an END_WORKFLOW step exists
    end_step_ids = [s["id"] for s in normalized_steps if s.get("type") == "END_WORKFLOW"]
    default_end_id = end_step_ids[0] if end_step_ids else "end_workflow"
    if not end_step_ids:
        normalized_steps.append(
            {
                "id": default_end_id,
                "title": "End Workflow",
                "type": "END_WORKFLOW",
            }
        )

    all_step_ids = {s["id"] for s in normalized_steps}

    # Auto-wire any non-END_WORKFLOW step that has empty transitions or targets unknown step IDs
    for idx, s in enumerate(normalized_steps):
        if s.get("type") == "END_WORKFLOW":
            s.pop("actions", None)
            s.pop("transitions", None)
            continue
        valid_trs: list[dict[str, Any]] = []
        for tr in s.get("transitions") or []:
            tgt = tr.get("targetStepId") or ""
            if tgt not in all_step_ids:
                tgt = default_end_id
            valid_trs.append({**tr, "targetStepId": tgt})
        if not valid_trs:
            next_id = (
                normalized_steps[idx + 1]["id"]
                if (idx + 1) < len(normalized_steps)
                else default_end_id
            )
            if next_id == s["id"]:
                next_id = default_end_id
            valid_trs.append({"targetStepId": next_id})
        s["transitions"] = valid_trs

    raw_root = str(wf_payload.get("rootStepId") or "").strip()
    if raw_root:
        raw_root = re.sub(r"[^a-zA-Z0-9_-]", "_", raw_root).strip("_")
    root_step_id = (
        raw_root if (raw_root and raw_root in all_step_ids) else normalized_steps[0]["id"]
    )

    # Verify DAG reachability & break any accidental cycles (required by PublishCompanionAgentWorkflow)
    step_by_id = {s["id"]: s for s in normalized_steps}
    visited: set[str] = set()
    visiting: set[str] = set()

    def _dfs(curr_id: str) -> None:
        visiting.add(curr_id)
        visited.add(curr_id)
        st_obj = step_by_id.get(curr_id)
        if st_obj and st_obj.get("type") != "END_WORKFLOW":
            clean_trs: list[dict[str, Any]] = []
            for tr in st_obj.get("transitions") or []:
                tgt = tr.get("targetStepId")
                if not tgt or tgt not in step_by_id:
                    tgt = default_end_id
                    tr = {**tr, "targetStepId": tgt}
                if tgt in visiting:
                    # Break cycle by routing back-edge to default_end_id
                    tr = {**tr, "targetStepId": default_end_id}
                    tgt = default_end_id
                clean_trs.append(tr)
                if tgt not in visited:
                    _dfs(tgt)
            st_obj["transitions"] = clean_trs
        visiting.remove(curr_id)

    _dfs(root_step_id)

    # Prune unreachable orphan steps so PublishCompanionAgentWorkflow never fails
    reachable_steps = [s for s in normalized_steps if s["id"] in visited]
    if not any(s.get("type") == "END_WORKFLOW" for s in reachable_steps):
        end_obj = step_by_id.get(default_end_id) or {
            "id": default_end_id,
            "title": "End Workflow",
            "type": "END_WORKFLOW",
        }
        reachable_steps.append(end_obj)
        for s in reversed(reachable_steps):
            if s.get("type") != "END_WORKFLOW":
                s.setdefault("transitions", []).append({"targetStepId": default_end_id})
                break

    out: dict[str, Any] = {}
    if wf_payload.get("name"):
        out["name"] = str(wf_payload["name"]).strip()
    out["displayName"] = display_name
    out["description"] = description
    out["rootStepId"] = root_step_id
    out["steps"] = reachable_steps
    return out


def extract_text_from_pdf_file(pdf_path: str | Path) -> str:
    """Extracts plain text from a `.pdf` SOP file using `pdftotext` (if installed) or pure-Python PDF stream parsing."""
    p = Path(pdf_path).expanduser().resolve()
    raw_bytes = p.read_bytes()

    # 1. Try system `pdftotext` if available for layout-preserving extraction
    pdftotext_bin = shutil.which("pdftotext")
    if pdftotext_bin:
        try:
            proc = subprocess.run(
                [pdftotext_bin, "-layout", str(p), "-"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except Exception:
            pass

    # 2. Pure-Python PDF stream & text operator (`Tj` / `TJ`) extractor
    extracted_chunks: list[str] = []
    stream_pattern = re.compile(rb"stream[\r\n]+(.*?)[\r\n]+endstream", re.DOTALL)

    def _decode_pdf_literals(content_bytes: bytes) -> list[str]:
        text_out: list[str] = []
        decoded_str = content_bytes.decode("latin-1", errors="ignore")
        for m in re.finditer(r"\(([^()\\]*(?:\\.[^()\\]*)*)\)\s*Tj|\[((?:[^\[\]]|\([^)]*\))*)\]\s*TJ", decoded_str):
            if m.group(1) is not None:
                s = m.group(1)
            else:
                # Concatenate parenthesized literals inside TJ array
                s = "".join(re.findall(r"\(([^()\\]*(?:\\.[^()\\]*)*)\)", m.group(2) or ""))
            s = (
                s.replace(r"\(", "(")
                .replace(r"\)", ")")
                .replace(r"\n", "\n")
                .replace(r"\r", "\r")
                .replace(r"\\", "\\")
            )
            if s.strip():
                text_out.append(s.strip())
        return text_out

    for sm in stream_pattern.finditer(raw_bytes):
        raw_stream = sm.group(1)
        candidates = [raw_stream]
        try:
            candidates.insert(0, zlib.decompress(raw_stream))
        except Exception:
            pass
        for cand in candidates:
            lits = _decode_pdf_literals(cand)
            if lits:
                extracted_chunks.extend(lits)
                break

    if not extracted_chunks:
        # Fallback: scan top-level uncompressed PDF content
        extracted_chunks.extend(_decode_pdf_literals(raw_bytes))

    return "\n".join(extracted_chunks).strip()


def parse_sop_text_to_workflow_spec(
    sop_text: str,
    default_title: str = "SOP Companion Workflow",
    known_tools: Optional[set[str]] = None,
) -> dict[str, Any]:
    """Parses extracted PDF/Markdown SOP text into a structured workflow specification dict."""
    lines = [ln.strip() for ln in (sop_text or "").splitlines() if ln.strip()]
    if not lines:
        raise ValueError("Extracted SOP document text is empty.")

    known_tools_lower = {t.lower(): t for t in (known_tools or set())}
    title = default_title
    description = ""
    raw_steps_text: list[tuple[str, str]] = []

    step_re = re.compile(
        r"^(?:step\s*(\d+)\s*[:.\-–—]\s*|(\d+)\s*[.)\-–—]\s*)(.+)$",
        re.IGNORECASE,
    )

    for idx, line in enumerate(lines):
        clean_heading = re.sub(r"^#+\s*", "", line).strip()
        if idx == 0 and not step_re.match(clean_heading):
            title = clean_heading[:63]
            continue
        low = clean_heading.lower()
        if (
            low.startswith("trigger:")
            or low.startswith("when to use:")
            or low.startswith("purpose:")
            or low.startswith("entry criteria:")
        ):
            description = clean_heading.split(":", 1)[1].strip()
            continue

        m = step_re.match(clean_heading)
        if m:
            step_body = m.group(3).strip()
            if ":" in step_body:
                st_title, st_detail = step_body.split(":", 1)
                raw_steps_text.append((st_title.strip(), st_detail.strip() or st_title.strip()))
            else:
                raw_steps_text.append((step_body[:55].strip(), step_body))
        elif raw_steps_text:
            # Append continuation line to the current step's detail
            prev_t, prev_d = raw_steps_text[-1]
            raw_steps_text[-1] = (prev_t, f"{prev_d} {clean_heading}".strip())
        elif not description:
            description = clean_heading

    if not description:
        description = f"Trigger when the customer requests assistance with {title}."

    if not raw_steps_text:
        # Treat each non-title line as a sequential step
        for idx, ln in enumerate(lines[1:], start=1):
            raw_steps_text.append((f"Step {idx}", ln))

    spec_steps: list[dict[str, Any]] = []
    for idx, (st_title, st_detail) in enumerate(raw_steps_text, start=1):
        combined = f"{st_title} {st_detail}"
        comb_low = combined.lower()
        step_id = f"step_{idx}_{re.sub(r'[^a-z0-9]+', '_', st_title.lower()).strip('_')[:24]}"

        # Check for explicit tool reference (`{@TOOL:xyz}`, `tool: xyz`, or known bundle tool name)
        explicit_tool_m = re.search(
            r"(?:\{@TOOL:([a-zA-Z0-9_/-]+)\}|\b(?:invoke|call|run|execute)\s+`?([a-z][a-z0-9_]{2,})`?)",
            combined,
            re.IGNORECASE,
        )
        matched_tool = ""
        if explicit_tool_m:
            cand = (explicit_tool_m.group(1) or explicit_tool_m.group(2) or "").strip()
            if cand.lower() not in ("the", "tool", "api", "system", "function", "customer"):
                matched_tool = known_tools_lower.get(cand.lower(), cand)
        if not matched_tool:
            for kt_low, kt_orig in known_tools_lower.items():
                if kt_low in comb_low:
                    matched_tool = kt_orig
                    break

        if any(k in comb_low for k in ("end workflow", "complete workflow", "close workflow")):
            spec_steps.append(
                {
                    "id": "end_workflow",
                    "title": "End Workflow",
                    "type": "END_WORKFLOW",
                }
            )
        elif matched_tool:
            is_mut = any(
                matched_tool.lower().startswith(p)
                for p in ("issue_", "refund_", "cancel_", "update_", "create_", "delete_", "waive_", "credit_")
            ) or ("confirmationrequirement=required" in comb_low.replace(" ", ""))
            out_var_name = f"{re.sub(r'[^a-z0-9_]', '_', matched_tool.lower())}_status"
            spec_steps.append(
                {
                    "id": step_id,
                    "title": st_title,
                    "type": "SYSTEM_ACTION",
                    "tool": matched_tool,
                    "confirmationRequirement": "REQUIRED" if is_mut else "NOT_REQUIRED",
                    "displayDetails": st_detail,
                    "outputVariableMappings": [
                        {
                            "name": out_var_name,
                            "description": f"Output status from {matched_tool}.",
                            "fieldPath": "status",
                        }
                    ],
                }
            )
        elif any(
            k in comb_low
            for k in ("ask ", "collect ", "obtain ", "request from caller", "verify with caller", "ask the caller")
        ):
            # Extract backticked or snake_case variable names if present
            found_vars = re.findall(r"`([a-zA-Z][a-zA-Z0-9_]*)`|\b([a-z]+_[a-z0-9_]+)\b", combined)
            var_list: list[dict[str, Any]] = []
            seen_step_vars: set[str] = set()
            for g1, g2 in found_vars:
                v_cand = (g1 or g2).strip()
                v_low = v_cand.lower()
                if v_cand and v_low not in ("step_type", "end_workflow") and v_low not in seen_step_vars:
                    seen_step_vars.add(v_low)
                    var_list.append(
                        {
                            "name": v_low,
                            "description": f"Customer {v_cand.replace('_', ' ')} collected during {st_title}.",
                            "type": "NUMBER" if "amount" in v_low else "STRING",
                        }
                    )
            if not var_list:
                var_list.append(
                    {
                        "name": f"collected_{idx}_value",
                        "description": st_detail,
                        "type": "STRING",
                    }
                )
            spec_steps.append(
                {
                    "id": step_id,
                    "title": st_title,
                    "type": "INFORMATION_COLLECTION",
                    "displayDetails": st_detail,
                    "prompt": st_detail,
                    "variables": var_list,
                }
            )
        elif any(k in comb_low for k in ("manual", "portal", "external system", "crm tab")):
            spec_steps.append(
                {
                    "id": step_id,
                    "title": st_title,
                    "type": "MANUAL_ACTION",
                    "displayDetails": st_detail,
                }
            )
        else:
            spec_steps.append(
                {
                    "id": step_id,
                    "title": st_title,
                    "type": "INFORMATIONAL_MESSAGE",
                    "displayDetails": st_detail,
                    "prompt": st_detail,
                }
            )

    return {
        "displayName": title[:63],
        "description": description,
        "rootStepId": spec_steps[0]["id"] if spec_steps else "step_1",
        "steps": spec_steps,
    }


def _generate_workflow_via_vertex(
    client: CompanionAgentRestClient,
    known_tools: set[str],
    pdf_path: Optional[Path] = None,
    sop_text: Optional[str] = None,
    user_prompt: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Uses Vertex AI Gemini (`gemini-2.5-flash`) to convert a PDF SOP, text SOP, and/or user `--prompt` into a workflow spec."""
    try:
        tools_hint = (
            f"Available tools in bundle: {sorted(known_tools)}"
            if known_tools
            else "Infer appropriate snake_case tool names for any system/API steps."
        )
        custom_directive = (
            f"\nAdditional User Prompt / Instructions:\n{user_prompt.strip()}\n"
            if user_prompt and user_prompt.strip()
            else ""
        )
        sop_context = (
            f"\nSource SOP Text:\n{sop_text.strip()}\n"
            if sop_text and sop_text.strip()
            else ""
        )
        prompt = (
            "You are the Google Cloud Companion Agent Workflow Architect.\n"
            "Generate a Dialogflow v2beta1 CompanionAgentWorkflow JSON object from the provided input.\n"
            "Rules:\n"
            "1. `displayName` must be <= 63 characters.\n"
            "2. `description` must state positive customer-intent trigger criteria.\n"
            "3. `steps` must use `type` values from: INFORMATION_COLLECTION, SYSTEM_ACTION, "
            "INFORMATIONAL_MESSAGE, MANUAL_ACTION, END_WORKFLOW.\n"
            "4. Every path must terminate at an END_WORKFLOW step with no cycles and no orphan steps.\n"
            f"5. {tools_hint}\n"
            f"{sop_context}"
            f"{custom_directive}"
            "Return ONLY valid JSON."
        )
        parts: list[dict[str, Any]] = []
        if pdf_path is not None and pdf_path.is_file():
            pdf_b64 = base64.b64encode(pdf_path.read_bytes()).decode("ascii")
            parts.append({"inlineData": {"mimeType": "application/pdf", "data": pdf_b64}})
        parts.append({"text": prompt})

        url = (
            f"https://{client.location}-aiplatform.googleapis.com/v1/projects/{client.project_id}"
            f"/locations/{client.location}/publishers/google/models/gemini-2.5-flash:generateContent"
        )
        resp = client.request(
            "POST",
            url,
            json_body={
                "contents": [
                    {
                        "role": "user",
                        "parts": parts,
                    }
                ],
                "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
            },
        )
        cand_text = (
            (((resp.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [{}])[0].get("text")
            or ""
        )
        parsed = json.loads(cand_text)
        if isinstance(parsed, dict):
            return parsed.get("workflow") if isinstance(parsed.get("workflow"), dict) else parsed
    except Exception:
        pass
    return None


def generate_workflow_from_spec(
    spec_path: Optional[str | Path] = None,
    bundle_dir: Optional[str | Path] = None,
    client: Optional[CompanionAgentRestClient] = None,
    use_vertex: bool = False,
    user_prompt: Optional[str] = None,
) -> dict[str, Any]:
    """Loads a `.pdf` SOP document, `.md`/`.txt` SOP, `.json` workflow spec, and/or `--prompt` text,
    compiles a DAG-verified `v2beta1` `CompanionAgentWorkflow`, and optionally links it into `--bundle`.
    """
    if not spec_path and not (user_prompt and user_prompt.strip()):
        raise ValueError("Either a `spec` file path (.pdf, .md, .txt, .json) or `--prompt` must be provided.")

    p = Path(spec_path).expanduser().resolve() if spec_path else None
    suffix = p.suffix.lower() if p else ""

    tool_path_map: dict[str, str] = {}
    known_tools: set[str] = set()
    project_id = "sample-proj"
    location = "global"

    if bundle_dir:
        b_dir = Path(bundle_dir).expanduser().resolve()
        manifest_file = b_dir / "manifest.json"
        if manifest_file.is_file():
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            project_id = manifest.get("projectId") or project_id
            location = manifest.get("location") or location
        tools_dir = b_dir / "tools"
        if tools_dir.is_dir():
            for tf in sorted(tools_dir.glob("*.json")):
                t_data = json.loads(tf.read_text(encoding="utf-8"))
                t_name = t_data.get("name") or ""
                t_short = t_data.get("displayName") or t_data.get("shortName") or tf.stem
                if t_short:
                    known_tools.add(t_short)
                if t_name and t_short:
                    tool_path_map[t_short] = t_name
                    tool_path_map[t_short.lower()] = t_name

    wf_input: Optional[dict[str, Any]] = None
    if p is None:
        # Prompt-only mode (no file path provided)
        if use_vertex and client is not None:
            wf_input = _generate_workflow_via_vertex(
                client=client,
                known_tools=known_tools,
                user_prompt=user_prompt,
            )
        if wf_input is None:
            wf_input = parse_sop_text_to_workflow_spec(
                user_prompt or "",
                default_title="Custom Prompt Workflow",
                known_tools=known_tools,
            )
    elif suffix == ".pdf":
        if use_vertex and client is not None:
            wf_input = _generate_workflow_via_vertex(
                client=client,
                known_tools=known_tools,
                pdf_path=p,
                user_prompt=user_prompt,
            )
        if wf_input is None:
            sop_text = extract_text_from_pdf_file(p)
            if user_prompt and user_prompt.strip():
                sop_text = f"{sop_text}\n{user_prompt.strip()}"
            wf_input = parse_sop_text_to_workflow_spec(
                sop_text,
                default_title=p.stem.replace("_", " ").title(),
                known_tools=known_tools,
            )
    elif suffix in (".md", ".txt"):
        sop_text = p.read_text(encoding="utf-8")
        if use_vertex and client is not None:
            wf_input = _generate_workflow_via_vertex(
                client=client,
                known_tools=known_tools,
                sop_text=sop_text,
                user_prompt=user_prompt,
            )
        if wf_input is None:
            if user_prompt and user_prompt.strip():
                sop_text = f"{sop_text}\n{user_prompt.strip()}"
            wf_input = parse_sop_text_to_workflow_spec(
                sop_text,
                default_title=p.stem.replace("_", " ").title(),
                known_tools=known_tools,
            )
    else:
        raw_spec = json.loads(p.read_text(encoding="utf-8"))
        wf_input = (
            raw_spec.get("workflow") if isinstance(raw_spec.get("workflow"), dict) else raw_spec
        )

    normalized_wf = normalize_workflow_payload(wf_input, tool_path_map=tool_path_map)
    wf_slug = (
        re.sub(r"[^a-z0-9]+", "-", normalized_wf["displayName"].lower()).strip("-") or "workflow"
    )
    if "name" not in normalized_wf:
        normalized_wf["name"] = (
            f"projects/{project_id}/locations/{location}/companionAgentWorkflows/{wf_slug}"
        )

    if bundle_dir:
        b_dir = Path(bundle_dir).expanduser().resolve()
        wf_dir = b_dir / "workflows"
        wf_dir.mkdir(parents=True, exist_ok=True)
        wf_file = wf_dir / f"{wf_slug.replace('-', '_')}.json"
        wf_file.write_text(json.dumps(normalized_wf, indent=2) + "\n", encoding="utf-8")

        ca_file = b_dir / "companion_agent.json"
        if ca_file.is_file():
            ca_data = json.loads(ca_file.read_text(encoding="utf-8"))
            specs = list(ca_data.get("companionAgentWorkflowSpecs") or [])
            if not any(
                isinstance(s, dict) and s.get("workflow") == normalized_wf["name"] for s in specs
            ):
                specs.append(
                    {
                        "workflow": normalized_wf["name"],
                        "proactiveEnabled": True,
                        "reactiveEnabled": True,
                    }
                )
            ca_data["companionAgentWorkflowSpecs"] = specs
            ca_file.write_text(json.dumps(ca_data, indent=2) + "\n", encoding="utf-8")

    return normalized_wf
