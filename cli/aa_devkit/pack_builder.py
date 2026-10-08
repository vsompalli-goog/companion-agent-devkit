"""Single-source skills & rules pack compiler (FR-1.1).

Compiles the DevKit's single-source `pack/` directory into AI coding agent
customization directories:
  - `.agent/` (for Jetski: `.agent/rules/` and `.agent/skills/`)
  - `.claude/` (for customer Claude Code workspaces: `.claude/CLAUDE.md` and `.claude/skills/`)
"""

from __future__ import annotations

from pathlib import Path
import shutil

CLAUDE_PRIVACY_BANNER = """<!--
IMPORTANT PRIVACY & COMPLIANCE NOTICE (FR-1.1):
Third-party AI coding packages (such as `.claude`) are strictly for customers' own use
within their own environment. Google staff must NEVER load customer data into third-party AI tools.
-->
"""


def get_default_pack_source() -> Path:
    """Returns the absolute path to the single-source `pack/` folder in this repository."""
    return Path(__file__).resolve().parents[2] / "pack"


def _strip_yaml_frontmatter(markdown_text: str) -> str:
    if not markdown_text.startswith("---"):
        return markdown_text
    parts = markdown_text.split("---", 2)
    if len(parts) == 3:
        return parts[2].lstrip("\n")
    return markdown_text


def install_pack(
    dest_root: str | Path,
    target: str = "all",
    pack_source: str | Path | None = None,
) -> list[Path]:
    """Installs the Companion Agent DevKit pack into `.agent`, `.claude`, or both (`all`)."""
    src = Path(pack_source).expanduser().resolve() if pack_source else get_default_pack_source()
    if not src.exists():
        raise FileNotFoundError(f"Pack source directory not found: {src}")

    dest = Path(dest_root).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)

    normalized_target = target.strip().lower().lstrip(".")
    if normalized_target not in ("agent", "claude", "all"):
        raise ValueError(f"Invalid target '{target}'. Expected 'agent', 'claude', or 'all'.")

    installed_paths: list[Path] = []
    rules_src = src / "rules"
    skills_src = src / "skills"

    if normalized_target in ("agent", "all"):
        agent_dir = dest / ".agent"
        agent_rules_dir = agent_dir / "rules"
        agent_skills_dir = agent_dir / "skills"
        agent_rules_dir.mkdir(parents=True, exist_ok=True)
        agent_skills_dir.mkdir(parents=True, exist_ok=True)

        if rules_src.exists():
            for rule_file in sorted(rules_src.glob("*.md")):
                target_file = agent_rules_dir / rule_file.name
                shutil.copy2(rule_file, target_file)
                installed_paths.append(target_file)

        if skills_src.exists():
            for skill_dir in sorted(skills_src.iterdir()):
                if not skill_dir.is_dir():
                    continue
                target_skill_dir = agent_skills_dir / skill_dir.name
                if target_skill_dir.exists():
                    shutil.rmtree(target_skill_dir)
                shutil.copytree(skill_dir, target_skill_dir)
                installed_paths.append(target_skill_dir)

    if normalized_target in ("claude", "all"):
        claude_dir = dest / ".claude"
        claude_skills_dir = claude_dir / "skills"
        claude_skills_dir.mkdir(parents=True, exist_ok=True)

        # Compile rules into .claude/CLAUDE.md with mandatory privacy notice
        claude_md_sections = [CLAUDE_PRIVACY_BANNER.strip()]
        if rules_src.exists():
            for rule_file in sorted(rules_src.glob("*.md")):
                raw_text = rule_file.read_text(encoding="utf-8")
                claude_md_sections.append(_strip_yaml_frontmatter(raw_text).strip())

        claude_md_path = claude_dir / "CLAUDE.md"
        claude_md_path.write_text("\n\n".join(claude_md_sections) + "\n", encoding="utf-8")
        installed_paths.append(claude_md_path)

        if skills_src.exists():
            for skill_dir in sorted(skills_src.iterdir()):
                if not skill_dir.is_dir():
                    continue
                target_skill_dir = claude_skills_dir / skill_dir.name
                if target_skill_dir.exists():
                    shutil.rmtree(target_skill_dir)
                shutil.copytree(skill_dir, target_skill_dir)
                installed_paths.append(target_skill_dir)

    return installed_paths
