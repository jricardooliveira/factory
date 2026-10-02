"""Prompt blocks shared across agents. Each returns '' when it does not apply.

Prompt TEXT is production behaviour: `tests/pipeline/prompts/test_prompt_golden.py`
pins every assembled prompt byte-for-byte.
"""

from __future__ import annotations

from pathlib import Path

from factory.domain.gates import MAX_CODER_ATTEMPTS
from factory.evidence.adr import load_project_memory
from factory.pipeline.state import PipelineState
from factory.workspace.repo_map import build_repo_inventory


def _project_context(state: PipelineState) -> str:
    """The rendered project spec as a leading prompt block ('' off-project)."""
    return f"{state['project_spec']}\n\n" if state.get("project_spec") else ""


def _project_memory_block(state: PipelineState) -> str:
    """Prior ADRs + PROJECT_RULES for this project, as a prompt block ('' if none)."""
    project_dir = state.get("project_dir")
    if not project_dir:
        return ""
    memory = load_project_memory(Path(project_dir), exclude_story=state.get("story_id"))
    return f"{memory}\n\n" if memory else ""


def _repo_inventory_block(state: PipelineState) -> str:
    """Interface map of the existing repo, so agents design/code against what's
    really there (brownfield awareness). Empty on a greenfield repo."""
    inventory = build_repo_inventory(Path(state.get("opencode_cwd") or "."))
    if not inventory:
        return ""
    return (
        "## Existing codebase (interfaces only — integrate with these, do not rewrite)\n\n"
        f"{inventory}\n\n"
    )


def _reviewer_feedback_block(state: PipelineState) -> str:
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
    else:
        return ""
    lines = "\n".join(f"- {f}" for f in findings)
    return f"{header}\n\n{intro}\n{lines}\n\n"


def _retry_context_block(state: PipelineState, attempt: int) -> str:
    """Prompt block telling the coder this is a remediation attempt ('' on first try)."""
    findings = state.get("prior_findings") or []
    if attempt <= 1 or not findings:
        return ""
    trigger = state.get("triggered_by", "a gate")
    lines = "\n".join(f"- {f}" for f in findings)
    return (
        f"## Previous attempt failed (attempt {attempt} of {MAX_CODER_ATTEMPTS})\n\n"
        f"The prior implementation failed `{trigger}`. Fix these specifically; "
        f"the files you wrote already exist in the working directory — correct them:\n"
        f"{lines}\n\n"
    )
