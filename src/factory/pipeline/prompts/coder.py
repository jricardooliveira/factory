"""The coder-agent's prompts: one per task, and the tester-driven remediation pass.

The packs themselves are rendered by `context_pack`; this module gathers the
state-derived blocks that surround them.
"""

from __future__ import annotations

from factory.domain.contracts import SpecOutput, TaskDef
from factory.pipeline.prompts.blocks import (
    project_context_block,
    project_memory_block,
    repo_inventory_block,
    retry_context_block,
)
from factory.pipeline.prompts.context_pack import build_remediation_pack, build_task_pack
from factory.pipeline.state import PipelineState


def build_coder_task_prompt(
    state: PipelineState,
    task: TaskDef,
    spec: SpecOutput,
    *,
    task_index: int,
    task_count: int,
    completed: list[str],
    attempt: int,
) -> str:
    """The prompt for ONE task of the dependency-ordered plan (retry-aware)."""
    return build_task_pack(
        task,
        spec,
        state["architect"],
        project_context=project_context_block(state),
        memory_context=project_memory_block(state),
        repo_context=repo_inventory_block(state),
        retry_context=retry_context_block(state, attempt),
        completed=completed,
        position=(task_index + 1, task_count),
    )


def build_remediation_prompt(state: PipelineState, spec: SpecOutput, diff: str) -> str:
    """The prompt for a cross-cutting pass that resolves the tester's findings."""
    return build_remediation_pack(
        spec,
        state["architect"],
        state.get("prior_findings") or [],
        diff,
        project_context=project_context_block(state),
        memory_context=project_memory_block(state),
        rejected_release=state.get("triggered_by") == "release-rejected",
    )
