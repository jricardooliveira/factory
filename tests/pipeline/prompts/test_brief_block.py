"""The approved product brief reaches every agent that defines or builds the work.

`docs/work/BRIEF.md` existing IS the approval, so the prompt builders read it from
the project directory. Without it every prompt must stay byte-identical (the
golden fixtures pin that for the full prompts; here it is pinned per builder).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from factory.domain.contracts import SpecOutput
from factory.evidence.brief import BRIEF_RELPATH
from factory.pipeline import build_spec_prompt, build_tester_prompt
from factory.pipeline.prompts.architect import build_architect_prompt
from factory.pipeline.prompts.blocks import project_memory_block
from factory.pipeline.prompts.coder import build_coder_task_prompt

BRIEF = "# Product brief — Ledger\n\n## Goal\n\n- **What is it for?** Chasing late invoices\n"
HEADING = (
    "## Product brief (approved by the operator — the definition of what to build; "
    "do not contradict it)"
)
SPEC = {
    "verdict": "pass",
    "story_id": "US-0099",
    "title": "Overdue invoices",
    "problem": "Operators cannot see which invoices are late.",
    "why": "Late invoices cost money.",
    "acceptance_criteria": ["An invoice unpaid 30 days after issue is listed as overdue"],
    "tasks": [{"id": "T-0001", "title": "Overdue query", "purpose": "find late invoices"}],
    "questions": [],
}
ARCHITECT = {
    "verdict": "pass",
    "architecture_notes": "Add ledger/overdue.py.",
    "modules_affected": ["ledger/overdue.py"],
    "risks": [],
}


def _state(project_dir: Path) -> dict[str, Any]:
    return {
        "request": "Show overdue invoices",
        "project_dir": str(project_dir),
        "opencode_cwd": str(project_dir),
        "story_id": "US-0001",
        "spec": SPEC,
        "architect": ARCHITECT,
    }


def _coder(state: dict[str, Any]) -> str:
    spec = SpecOutput.model_validate(SPEC)
    return build_coder_task_prompt(
        state, spec.tasks[0], spec, task_index=0, task_count=1, completed=[], attempt=1
    )


BUILDERS = {
    "spec": build_spec_prompt,
    "architect": build_architect_prompt,
    "coder": _coder,
    "tester": build_tester_prompt,
}


def _write_brief(project_dir: Path) -> None:
    path = project_dir / BRIEF_RELPATH
    path.parent.mkdir(parents=True)
    path.write_text(BRIEF, encoding="utf-8")


@pytest.mark.parametrize("agent", BUILDERS)
def test_prompt_carries_the_approved_brief(agent: str, tmp_path: Path) -> None:
    # Rules present in both runs, so the spec prompt's "within the constraints
    # above" trailer does not differ and the brief is the ONLY difference.
    (tmp_path / "PROJECT_RULES.md").write_text("Use sqlite only.\n", encoding="utf-8")
    state = _state(tmp_path)
    without = BUILDERS[agent](state)
    assert "Product brief" not in without

    _write_brief(tmp_path)
    with_brief = BUILDERS[agent](state)

    brief_block = f"{HEADING}\n\n{BRIEF.strip()}\n\n"
    assert brief_block in with_brief
    assert with_brief.replace(brief_block, "", 1) == without


def test_brief_comes_before_the_existing_memory(tmp_path: Path) -> None:
    (tmp_path / "PROJECT_RULES.md").write_text("Use sqlite only.\n", encoding="utf-8")
    state = _state(tmp_path)
    memory_only = project_memory_block(state)

    _write_brief(tmp_path)
    block = project_memory_block(state)

    assert block == f"{HEADING}\n\n{BRIEF.strip()}\n\n{memory_only}"


def test_no_project_dir_means_no_block() -> None:
    assert project_memory_block({"request": "x"}) == ""
