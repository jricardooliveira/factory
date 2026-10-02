"""The tester-agent's prompt, including the changes it reviews."""

from __future__ import annotations

import json
from pathlib import Path

from factory.agent_config import review_policy
from factory.pipeline.prompts.blocks import project_context_block, project_memory_block
from factory.pipeline.state import PipelineState, factory_owned_paths
from factory.workspace.git import collect_repo_diff


def _changes_under_review_block(state: PipelineState) -> str:
    """The artifact the tester judges: the REAL cumulative git diff of every task's
    change when available, falling back to the agent's self-report off-git. A
    project's factory-owned evidence is left out: the tester reviews code."""
    diff = collect_repo_diff(
        Path(state.get("opencode_cwd") or "."), exclude=factory_owned_paths(state)
    )
    if diff:
        return f"## Cumulative changes under review (real git diff)\n\n```diff\n{diff}\n```\n\n"
    if diff == "":
        return "## Cumulative changes under review\n\n(git repo is clean — no diff detected)\n\n"
    # Not a git repo — fall back to the last task's self-reported implementation.
    return (
        "## Implementation (self-reported; repo is not under git)\n\n"
        f"```json\n{json.dumps(state.get('coder', {}), indent=2)}\n```\n\n"
    )


def build_tester_prompt(state: PipelineState) -> str:
    """Assemble the tester's prompt, including the VERSIONED review policy.

    The review passes, the severity ladder and the skip list used to live as prose
    in `agents/tester-agent.md`, with the blocking threshold in
    `gates.py` — one policy in two places. `agents/policies/REVIEW.md` is now the
    single home; the agent definition keeps only the role and the JSON contract.
    """
    return (
        f"{project_context_block(state)}"
        f"{project_memory_block(state)}"
        f"{review_policy.policy_block()}"
        f"## Story\n\n```json\n{json.dumps(state.get('spec', {}), indent=2)}\n```\n\n"
        f"## Architecture\n\n```json\n{json.dumps(state.get('architect', {}), indent=2)}\n```\n\n"
        f"{_changes_under_review_block(state)}"
        f"## Build gate result\n\n{json.dumps(state.get('gate_build', {}), indent=2)}\n\n"
        "Review the implementation against the acceptance criteria and the review "
        "policy above, and emit your QA / security / performance sub-verdicts as JSON."
    )
