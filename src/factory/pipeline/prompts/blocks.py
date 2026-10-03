"""Prompt blocks shared across agents. Each returns '' when it does not apply.

Prompt TEXT is production behaviour: `tests/pipeline/prompts/test_prompt_golden.py`
pins every assembled prompt byte-for-byte.
"""

from __future__ import annotations

from pathlib import Path

from factory.domain.gates import MAX_CODER_ATTEMPTS, MAX_SCOPE_FILE_CHARS
from factory.evidence.adr import load_project_memory
from factory.evidence.brief import load_brief
from factory.pipeline.state import PipelineState, factory_owned_paths
from factory.workspace.layout import is_evidence_path
from factory.verification.scope import strip_repo_prefix
from factory.workspace.repo_map import build_repo_inventory, iter_code_files


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


def _file_section(rel: str, text: str, budget: int) -> str:
    notice = ""
    if len(text) > budget:
        notice = (
            f"\n… (truncated: showing {budget} of {len(text)} characters; "
            "keep the rest of the file intact)\n"
        )
        text = text[:budget]
    return f"### {rel}\n\n````\n{text}\n````{notice}\n"


def scope_files_block(state: PipelineState, scope: list[str]) -> str:
    """The CURRENT text of each file in `scope`, so a `modify` is an edit of what
    is there rather than a blind rewrite. '' when nothing applies.

    A directory entry expands to the code files under it, sharing ONE
    MAX_SCOPE_FILE_CHARS budget for the whole entry. Factory evidence is skipped
    (the coder may not write it), as is a glob or a path outside the project.
    """
    root = Path(state.get("opencode_cwd") or state.get("project_dir") or ".").resolve()
    owned = factory_owned_paths(state)
    sections: list[str] = []
    for rel in scope:
        rel = strip_repo_prefix(rel.strip()).rstrip("/")
        if not rel or "*" in rel or (owned and is_evidence_path(rel, owned)):
            continue
        path = (root / rel).resolve()
        if not path.is_relative_to(root):
            continue
        if path.is_dir():
            budget = MAX_SCOPE_FILE_CHARS
            for file, sub in iter_code_files(path):
                if budget <= 0:
                    sections.append(f"… (more files under {rel}/ not shown: size budget spent)\n")
                    break
                try:
                    text = file.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                sections.append(_file_section(f"{rel}/{sub.as_posix()}", text, budget))
                budget -= len(text)
            continue
        if not path.exists():
            sections.append(f"### {rel} (new file)\n")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        sections.append(_file_section(rel, text, MAX_SCOPE_FILE_CHARS))
    if not sections:
        return ""
    return (
        "## Current contents of files in scope (modify these; return the whole file)\n\n"
        + "\n".join(sections)
        + "\n"
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
        f"## Previous attempt failed (attempt {attempt} of {MAX_CODER_ATTEMPTS})\n\n"
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
