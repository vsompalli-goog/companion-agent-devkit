"""Unit tests for single-source pack builder (FR-1.1)."""

from __future__ import annotations

from pathlib import Path

from aa_devkit.pack_builder import install_pack


def test_install_pack_agent_and_claude(tmp_path: Path) -> None:
    installed = install_pack(dest_root=tmp_path, target="all")
    assert len(installed) > 0

    # Verify .agent (Jetski) structure
    agent_rule = tmp_path / ".agent" / "rules" / "companion-agent-guardrails.md"
    agent_arch_skill = tmp_path / ".agent" / "skills" / "aa-companion-architect" / "SKILL.md"
    agent_review_skill = tmp_path / ".agent" / "skills" / "aa-config-review" / "SKILL.md"
    assert agent_rule.is_file()
    assert agent_arch_skill.is_file()
    assert agent_review_skill.is_file()
    assert (tmp_path / ".agent" / "skills" / "aa-companion-architect" / "references" / "decision_guides.md").is_file()
    assert (tmp_path / ".agent" / "skills" / "aa-companion-architect" / "references" / "workflow_api_and_ui_concurrency.md").is_file()

    # Verify .claude structure & mandatory FR-1.1 customer-only privacy notice
    claude_md = tmp_path / ".claude" / "CLAUDE.md"
    claude_arch_skill = tmp_path / ".claude" / "skills" / "aa-companion-architect" / "SKILL.md"
    claude_review_skill = tmp_path / ".claude" / "skills" / "aa-config-review" / "SKILL.md"
    assert claude_md.is_file()
    assert claude_arch_skill.is_file()
    assert claude_review_skill.is_file()

    claude_text = claude_md.read_text(encoding="utf-8")
    assert "Google staff must NEVER load customer data into third-party AI tools" in claude_text
    assert "{@TOOL:tool_name}" in claude_text
