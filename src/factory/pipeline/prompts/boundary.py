"""The boundary-agent's prompt: the design to review, and why it needs reviewing."""

from __future__ import annotations

import json

from factory.domain.contracts import ArchitectOutput
from factory.domain.gates import boundary_review_reasons
from factory.pipeline.prompts.blocks import project_context_block, project_memory_block
from factory.pipeline.state import PipelineState


def build_boundary_prompt(state: PipelineState) -> str:
    """Assemble the boundary-agent's prompt.

    The project spec and rules come first: the tenant model, the roles and the
    API conventions a boundary review judges against live there, not in the story.
    """
    reasons = boundary_review_reasons(ArchitectOutput.model_validate(state["architect"]))
    why = "\n".join(f"- {r}" for r in reasons)
    return (
        f"{project_context_block(state)}"
        f"{project_memory_block(state)}"
        f"## Story\n\n```json\n{json.dumps(state['spec'], indent=2)}\n```\n\n"
        f"## Design under review\n\n```json\n{json.dumps(state['architect'], indent=2)}\n```\n\n"
        f"## Why this design needs a boundary review\n\n{why}\n\n"
        "Review the design's tenant, authorization, API-contract and security boundaries "
        "BEFORE any code is written, and emit your sub-verdicts as JSON."
    )
