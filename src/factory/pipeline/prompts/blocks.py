"""Prompt blocks shared across agents. Each returns '' when it does not apply.

Prompt TEXT is production behaviour: `tests/pipeline/prompts/test_prompt_golden.py`
pins every assembled prompt byte-for-byte.
"""

from __future__ import annotations

from pathlib import Path

from factory.agent_config.settings import settings
from factory.evidence.adr import load_project_memory
from factory.evidence.brief import load_brief
from factory.pipeline.state import PipelineState
from factory.workspace.repo_map import build_repo_inventory


def project_context_block(state: PipelineState) -> str:
    """The rendered project spec as a leading prompt block ('' off-project)."""
    return f"{state['project_spec']}\n\n" if state.get("project_spec") else ""


def project_memory_block(state: PipelineState) -> str:
    """The approved product brief, then prior ADRs + PROJECT_RULES ('' if none).

    The brief leads because it is the definition of the product; rules and ADRs
    are how it has been built so far. Without a BRIEF.md the block is unchanged.
    """
    project_dir = state.get("project_dir")
    if not project_dir:
        return ""
    brief = load_brief(Path(project_dir)).strip()
    brief_block = (
        "## Product brief (approved by the operator — the definition of what to build; "
        f"do not contradict it)\n\n{brief}\n\n"
        if brief
        else ""
    )
    memory = load_project_memory(Path(project_dir), exclude_story=state.get("story_id"))
    return brief_block + (f"{memory}\n\n" if memory else "")


def repo_inventory_block(state: PipelineState) -> str:
    """Interface map of the existing repo, so agents design/code against what's
    really there (brownfield awareness). Empty on a greenfield repo."""
    inventory = build_repo_inventory(Path(state.get("opencode_cwd") or "."))
    if not inventory:
        return ""
    return (
        "## Existing codebase (interfaces only — integrate with these, do not rewrite)\n\n"
        f"{inventory}\n\n"
    )


def reviewer_feedback_block(state: PipelineState) -> str:
    """Prompt block carrying feedback into a re-architecture pass.

    Two sources re-enter the architect with findings: a human REJECTING the
    architecture checkpoint, and the CODER reporting the design is infeasible.
    Either way the re-attempt must address the feedback, not re-propose the same
    design. Empty when neither applies.
    """
    trigger = state.get("triggered_by")
    findings = state.get("prior_findings") or []
    if not findings:
        return ""
    if trigger == "architecture-rejected":
        header = "## Reviewer feedback to address (your previous design was rejected)"
        intro = "Revise the design to resolve these points; do not simply re-propose it:"
    elif trigger == "design-infeasible":
        header = "## Implementer feedback (the coder could not build your previous design)"
        intro = "The coder found the design infeasible. Revise it to resolve:"
    elif trigger == "boundary-failed":
        header = "## Boundary review findings (your previous design failed the boundary review)"
        intro = (
            "The pre-implementation review found tenant / authorization / API contract / "
            "security problems. Revise the design so these hold:"
        )
    else:
        return ""
    lines = "\n".join(f"- {f}" for f in findings)
    return f"{header}\n\n{intro}\n{lines}\n\n"


def retry_context_block(state: PipelineState, attempt: int) -> str:
    """Prompt block telling the coder this is a remediation attempt ('' on first try)."""
    findings = state.get("prior_findings") or []
    if attempt <= 1 or not findings:
        return ""
    trigger = state.get("triggered_by", "a gate")
    lines = "\n".join(f"- {f}" for f in findings)
    return (
        f"## Previous attempt failed (attempt {attempt} of "
        f"{settings().budget.max_coder_attempts})\n\n"
        f"The prior implementation failed `{trigger}`. Fix these specifically; "
        f"the files you wrote already exist in the working directory — correct them:\n"
        f"{lines}\n\n"
    )


def boundary_rules_block(state: PipelineState) -> str:
    """The boundary review's rules for the implementation ('' when there was no review).

    The coder must follow them and the tester must check them: a boundary review
    that only gates the design, and never reaches the code, protects nothing.
    """
    if state.get("boundary_status") != "reviewed":
        return ""
    rules = (state.get("boundary") or {}).get("rules_for_coder") or []
    if not rules:
        return ""
    lines = "\n".join(f"- {r}" for r in rules)
    return (
        "## Boundary rules (from the pre-implementation boundary review — these MUST hold)\n\n"
        f"{lines}\n\n"
    )
