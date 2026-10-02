"""Per-task context packs (task ordering lives in factory.domain.task_order).

The coder implements ONE task per call against a minimal, task-scoped pack —
not the whole story at once. This bounds each LLM call (so it fits the timeout)
and makes the decomposition executional, not just descriptive.
See docs/contract/context-pack.template.md and EFFECTIVENESS.md §6.
"""

from __future__ import annotations

import json
from typing import Any

from factory.domain.contracts import SpecOutput, TaskDef


def build_task_pack(
    task: TaskDef,
    spec: SpecOutput,
    architect: dict[str, Any],
    *,
    project_context: str = "",
    memory_context: str = "",
    repo_context: str = "",
    retry_context: str = "",
    completed: list[str] | None = None,
    position: tuple[int, int] | None = None,
) -> str:
    """Assemble the minimal, scoped prompt for implementing a single task."""
    completed = completed or []
    pos = f" ({position[0]}/{position[1]})" if position else ""

    done_block = ""
    if completed:
        done_block = (
            "## Already implemented (do NOT recreate or modify these tasks)\n"
            + "\n".join(f"- {c}" for c in completed)
            + "\n\n"
        )

    scope_block = (
        "Allowed files/modules:\n" + "\n".join(f"- {s}" for s in task.scope) + "\n"
        if task.scope
        else "Allowed scope: (not restricted — stay within this task's purpose)\n"
    )

    ac_block = "\n".join(f"- {ac}" for ac in spec.acceptance_criteria) or "- (see task purpose)"

    return (
        f"{project_context}"
        f"{memory_context}"
        f"{repo_context}"
        f"{retry_context}"
        f"## Current task{pos}: {task.id} — {task.title}\n\n"
        f"Implement ONLY this task. Other tasks are handled in separate calls.\n\n"
        f"**Purpose:** {task.purpose}\n\n"
        f"{scope_block}\n"
        f"**Done when:** {task.completion_evidence or 'the task purpose is satisfied and the code compiles'}\n\n"
        f"{done_block}"
        f"## Story context\n\n"
        f"**Problem:** {spec.problem}\n\n"
        f"**Acceptance criteria (whole story — implement the parts this task owns):**\n{ac_block}\n\n"
        f"## Agreed architecture (follow this design)\n\n"
        f"```json\n{json.dumps(architect, indent=2)}\n```\n\n"
        "Output only the files needed for THIS task. Do not reimplement already-implemented "
        "tasks. Keep changes within the allowed scope."
    )


def build_remediation_pack(
    spec: SpecOutput,
    architect: dict[str, Any],
    findings: list[str],
    diff: str,
    *,
    project_context: str = "",
    memory_context: str = "",
) -> str:
    """Assemble the coder prompt for a tester-driven remediation pass.

    Unlike a per-task pack, this is cross-cutting: the implementation already
    exists and the coder must MODIFY it to resolve the QA/security findings.
    """
    findings_block = "\n".join(f"- {f}" for f in findings) or "- (see tester summary)"
    diff_block = f"```diff\n{diff}\n```\n\n" if diff else "(no diff available)\n\n"
    ac_block = "\n".join(f"- {ac}" for ac in spec.acceptance_criteria) or "- (see problem)"
    return (
        f"{project_context}"
        f"{memory_context}"
        "## Remediation pass — the QA / security / performance review FAILED\n\n"
        "The implementation already exists in the working directory. MODIFY the "
        "existing files to resolve every finding below. Do not rewrite unrelated "
        "code or add new features; return ONLY the changed files via code_blocks.\n\n"
        f"### Findings to resolve\n{findings_block}\n\n"
        f"## Current implementation under review (real diff)\n\n{diff_block}"
        f"## Story\n\n**Problem:** {spec.problem}\n\n"
        f"**Acceptance criteria:**\n{ac_block}\n\n"
        f"## Agreed architecture (follow this design)\n\n"
        f"```json\n{json.dumps(architect, indent=2)}\n```\n\n"
        "Set verdict 'complete' once all findings are addressed."
    )
