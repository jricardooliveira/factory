"""The architect-agent's prompt."""

from __future__ import annotations

import json

from factory.pipeline.prompts.blocks import (
    _project_context,
    _project_memory_block,
    _repo_inventory_block,
    _reviewer_feedback_block,
)
from factory.pipeline.state import PipelineState


def build_architect_prompt(state: PipelineState) -> str:
    """Assemble the architect-agent's prompt: project constraints, decision memory,
    the existing codebase, any reviewer/implementer feedback, then the story."""
    spec_context = f"## Story\n\n```json\n{json.dumps(state['spec'], indent=2)}\n```\n\n"
    return (
        f"{_project_context(state)}"
        f"{_project_memory_block(state)}"
        f"{_repo_inventory_block(state)}"
        f"{_reviewer_feedback_block(state)}"
        f"{spec_context}"
        f"## Original Request\n\n{state['request']}\n\n"
        "Design the technical approach for this story. "
        "You MUST stay within the project specification above, honor the Project "
        "Rules, and stay consistent with the Prior Architecture Decisions. "
        "Do not introduce technologies, patterns, or modules not listed in the spec."
    )
