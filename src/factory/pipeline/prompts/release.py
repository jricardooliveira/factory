"""The release-agent's prompt: everything the release notes must be true to."""

from __future__ import annotations

import json
from pathlib import Path

from factory.pipeline.state import PipelineState, factory_owned_paths
from factory.workspace.git import collect_repo_diff


def _diff_block(state: PipelineState) -> str:
    diff = collect_repo_diff(
        Path(state.get("opencode_cwd") or "."), exclude=factory_owned_paths(state)
    )
    if diff:
        return f"## The change being released (real git diff)\n\n```diff\n{diff}\n```\n\n"
    return "## The change being released\n\n(no git diff available)\n\n"


def _review_block(state: PipelineState) -> str:
    tester = state.get("tester") or {}
    keys = ("overall", "qa_verdict", "security_verdict", "highest_severity",
            "performance_verdict", "summary")
    return (
        "## Review verdict (tester-agent)\n\n"
        f"```json\n{json.dumps({k: tester[k] for k in keys if k in tester}, indent=2)}\n```\n\n"
    )


def build_release_prompt(state: PipelineState, evidence_gaps: list[str]) -> str:
    """Assemble the release-agent's prompt.

    `evidence_gaps` are the trust package's named blockers. The notes must not
    contradict them: an agent that writes "fully tested" over "tests were never
    executed" is the overstated evidence the trust package exists to prevent.
    """
    gaps = "\n".join(f"- {g}" for g in evidence_gaps) or "- none"
    return (
        f"## Story\n\n```json\n{json.dumps(state.get('spec', {}), indent=2)}\n```\n\n"
        f"## Architecture\n\n```json\n{json.dumps(state.get('architect', {}), indent=2)}\n```\n\n"
        f"{_diff_block(state)}"
        f"{_review_block(state)}"
        "## Evidence gaps the factory has already measured (do NOT contradict these)\n\n"
        f"{gaps}\n\n"
        "Write the release notes for this change as JSON."
    )
