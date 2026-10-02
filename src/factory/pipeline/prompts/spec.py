"""The spec-agent's prompt."""

from __future__ import annotations

from factory.pipeline.prompts.blocks import _project_memory_block
from factory.pipeline.state import PipelineState


def build_spec_prompt(state: PipelineState) -> str:
    """Assemble the spec-agent's prompt.

    It used to be the bare request string, which meant the story was written blind
    to the project's own hard constraints — the `forbidden` list, the NFRs, the
    existing modules, PROJECT_RULES. The same constraint block was already injected
    for the architect, the coder and the tester, so a criterion that contradicted a
    hard constraint got written at stage 2 and could only be silently dropped at
    stage 3. Constraints belong at the moment the work is DEFINED, which is the
    playbook's Stage-2 "skills applied as constraints" play.

    Also carries the operator's answers back in when a rejected spec is re-run, so
    a Checkpoint-1 rejection is a conversation rather than a dead end.
    """
    parts: list[str] = []
    if state.get("project_spec"):
        parts.append(f"{state['project_spec']}\n")
    memory = _project_memory_block(state)
    if memory:
        parts.append(memory)

    findings = state.get("prior_findings") or []
    if state.get("triggered_by") == "spec-rejected" and findings:
        parts.append(
            "## Operator feedback on your previous story (it was REJECTED)\n\n"
            "Re-specify the work taking these answers as settled. Do not re-ask "
            "them, and do not simply re-propose the same story:\n"
            + "\n".join(f"- {f}" for f in findings)
            + "\n"
        )

    parts.append(f"## Request\n\n{state['request']}")
    if len(parts) > 1:
        parts.append(
            "\nDefine the story WITHIN the constraints above. If a constraint makes "
            "the request impossible as stated, say so in `questions` rather than "
            "writing an acceptance criterion that violates it."
        )
    return "\n".join(parts)
