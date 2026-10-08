"""Stateless configuration bundle exporter and loader (FR-2.1).

Writes harvested Companion Agent graphs to a deterministic local directory or `.zip`
archive, and loads exported directories/zips into memory for L2 config review.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any
import zipfile


def _safe_filename(raw_id: str, fallback: str = "item") -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", (raw_id or "").strip()).strip("_")
    return clean or fallback


def build_bundle_file_map(graph: dict[str, Any]) -> dict[str, str]:
    """Serializes a harvested configuration graph into relative file paths -> JSON strings."""
    files: dict[str, str] = {}

    manifest = graph.get("manifest") or {}
    files["manifest.json"] = json.dumps(manifest, indent=2, sort_keys=True) + "\n"

    profile = graph.get("conversation_profile") or {}
    if profile:
        files["conversation_profile.json"] = json.dumps(profile, indent=2, sort_keys=True) + "\n"

    agent = graph.get("companion_agent") or {}
    if agent:
        files["companion_agent.json"] = json.dumps(agent, indent=2, sort_keys=True) + "\n"

    used_wf_names: set[str] = set()
    for idx, wf in enumerate(graph.get("workflows") or [], start=1):
        if not isinstance(wf, dict):
            continue
        raw_id = (wf.get("name") or "").split("/")[-1] or wf.get("displayName") or f"workflow_{idx}"
        base_id = _safe_filename(raw_id, f"workflow_{idx}")
        fname = base_id
        counter = 2
        while fname in used_wf_names:
            fname = f"{base_id}_{counter}"
            counter += 1
        used_wf_names.add(fname)
        files[f"workflows/{fname}.json"] = json.dumps(wf, indent=2, sort_keys=True) + "\n"

    used_tool_names: set[str] = set()
    for idx, tool in enumerate(graph.get("tools") or [], start=1):
        if not isinstance(tool, dict):
            continue
        source = str(tool.get("toolSource") or "").upper()
        subdir = "ces" if source == "CES" or "/apps/" in str(tool.get("name") or "") else "dialogflow"
        raw_id = (
            tool.get("shortName")
            or (tool.get("name") or "").split("/")[-1]
            or tool.get("displayName")
            or f"tool_{idx}"
        )
        base_id = _safe_filename(str(raw_id), f"tool_{idx}")
        rel_key = f"{subdir}/{base_id}"
        counter = 2
        while rel_key in used_tool_names:
            rel_key = f"{subdir}/{base_id}_{counter}"
            counter += 1
        used_tool_names.add(rel_key)
        files[f"tools/{rel_key}.json"] = json.dumps(tool, indent=2, sort_keys=True) + "\n"

    return files


def write_export_bundle(graph: dict[str, Any], output_path: str | Path) -> Path:
    """Writes the exported configuration graph to a local directory or `.zip` file."""
    target = Path(output_path).expanduser().resolve()
    file_map = build_bundle_file_map(graph)

    if target.suffix.lower() == ".zip":
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for rel_path, content in sorted(file_map.items()):
                zf.writestr(rel_path, content)
        return target

    target.mkdir(parents=True, exist_ok=True)
    for rel_path, content in sorted(file_map.items()):
        dest_file = target / rel_path
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        dest_file.write_text(content, encoding="utf-8")
    return target


def load_export_bundle(bundle_path: str | Path) -> dict[str, Any]:
    """Loads an exported configuration bundle from either a local folder or `.zip` archive."""
    source = Path(bundle_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Export bundle not found: {source}")

    raw_files: dict[str, Any] = {}

    if source.is_file() and source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source, mode="r") as zf:
            for name in zf.namelist():
                if not name.endswith(".json") or name.startswith("__MACOSX"):
                    continue
                norm_name = name.lstrip("/")
                raw_files[norm_name] = json.loads(zf.read(name).decode("utf-8"))
    elif source.is_dir():
        for json_file in sorted(source.rglob("*.json")):
            rel_name = json_file.relative_to(source).as_posix()
            raw_files[rel_name] = json.loads(json_file.read_text(encoding="utf-8"))
    else:
        raise ValueError(f"Unsupported bundle path (expected directory or .zip): {source}")

    # Normalize if zip had a single top-level wrapper folder
    if "manifest.json" not in raw_files and "companion_agent.json" not in raw_files:
        stripped_files: dict[str, Any] = {}
        for k, v in raw_files.items():
            parts = k.split("/", 1)
            if len(parts) == 2:
                stripped_files[parts[1]] = v
            else:
                stripped_files[k] = v
        if "manifest.json" in stripped_files or "companion_agent.json" in stripped_files:
            raw_files = stripped_files

    workflows: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    for rel_path, payload in sorted(raw_files.items()):
        if rel_path.startswith("workflows/") and isinstance(payload, dict):
            workflows.append(payload)
        elif rel_path.startswith("tools/") and isinstance(payload, dict):
            tools.append(payload)

    return {
        "manifest": raw_files.get("manifest.json") or {},
        "conversation_profile": raw_files.get("conversation_profile.json") or {},
        "companion_agent": raw_files.get("companion_agent.json") or {},
        "workflows": workflows,
        "tools": tools,
    }
