"""Golden-question regression evaluation runner for DevKit skills (FR-1.5).

Verifies that every skill and reference file in `pack/` satisfies all golden-question
concept coverage assertions in `evals/golden_questions.json`, and optionally scores
a JSON file of model responses (`{"GQ-001": "answer text", ...}`) against the golden set.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from aa_devkit.pack_builder import get_default_pack_source


def get_default_golden_questions_path() -> Path:
    return Path(__file__).resolve().parents[2] / "evals" / "golden_questions.json"


def run_skill_regression_eval(
    golden_path: Optional[str | Path] = None,
    pack_source: Optional[str | Path] = None,
    candidate_answers_path: Optional[str | Path] = None,
) -> dict[str, Any]:
    """Runs FR-1.5 regression checks over skill content (and optional candidate answers)."""
    g_path = Path(golden_path).resolve() if golden_path else get_default_golden_questions_path()
    p_src = Path(pack_source).resolve() if pack_source else get_default_pack_source()

    dataset = json.loads(g_path.read_text(encoding="utf-8"))
    questions = dataset.get("questions") or []

    candidate_answers: dict[str, str] = {}
    if candidate_answers_path:
        candidate_answers = json.loads(
            Path(candidate_answers_path).resolve().read_text(encoding="utf-8")
        )

    results: list[dict[str, Any]] = []
    passed_count = 0

    for q in questions:
        qid = q["id"]
        skill_name = q["skill"]
        ref_rel = q.get("reference") or ""
        must_contain = q.get("must_contain_concepts") or []
        must_reject = q.get("must_reject_concepts") or []

        skill_dir = p_src / "skills" / skill_name
        skill_md_path = skill_dir / "SKILL.md"
        ref_path = skill_dir / ref_rel if ref_rel else skill_md_path

        errors: list[str] = []
        if not skill_md_path.is_file():
            errors.append(f"Missing skill entrypoint: {skill_md_path}")
        if not ref_path.is_file():
            errors.append(f"Missing reference file: {ref_path}")

        target_text = ""
        if candidate_answers_path:
            target_text = str(candidate_answers.get(qid) or "")
            if not target_text:
                errors.append(f"Missing candidate answer for {qid}")
        elif ref_path.is_file() and skill_md_path.is_file():
            target_text = (
                skill_md_path.read_text(encoding="utf-8")
                + "\n"
                + ref_path.read_text(encoding="utf-8")
            )

        lower_text = target_text.lower()
        for concept in must_contain:
            if concept.lower() not in lower_text:
                errors.append(f"Missing required concept: '{concept}'")
        for forbidden in must_reject:
            if forbidden.lower() in lower_text:
                errors.append(f"Contains forbidden concept: '{forbidden}'")

        passed = len(errors) == 0
        if passed:
            passed_count += 1

        results.append(
            {
                "id": qid,
                "fr": q.get("fr", ""),
                "skill": skill_name,
                "passed": passed,
                "errors": errors,
            }
        )

    total = len(questions)
    return {
        "total": total,
        "passed": passed_count,
        "failed": total - passed_count,
        "pass_rate_pct": round((passed_count / max(1, total)) * 100.0, 1),
        "results": results,
    }
