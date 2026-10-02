"""LangGraph pipeline: spec-agent → gate-1 → architect-agent → gate-2 → coder-agent
→ tester-agent → gate-test.

This module is the package's PUBLIC API — code outside `factory.pipeline` imports
from here, never from the submodules (pinned by `tests/pipeline/test_public_api.py`):

    state.py        PipelineState, the graph state
    graph.py        conditional edges, resume routing, every graph builder
    nodes/          one module per agent stage + the gate nodes
    prompts/        every agent prompt (byte-pinned by test_prompt_golden.py)
    agent_calls.py  the single agent-call boundary (run or replay, parse JSON)
"""

from __future__ import annotations

from factory.pipeline.graph import (
    build_architect_resume_pipeline,
    build_coder_only_pipeline,
    build_pipeline,
    build_spec_resume_pipeline,
    compile_architect_resume_pipeline,
    compile_coder_only_pipeline,
    compile_pipeline,
    compile_release_pipeline,
    compile_spec_resume_pipeline,
    resume_entry_for,
)
from factory.pipeline.nodes.gates import settled_threshold_terms
from factory.pipeline.prompts.spec import build_spec_prompt
from factory.pipeline.prompts.tester import build_tester_prompt
from factory.pipeline.state import PipelineState

__all__ = [
    "PipelineState",
    "build_architect_resume_pipeline",
    "build_coder_only_pipeline",
    "build_pipeline",
    "build_spec_prompt",
    "build_spec_resume_pipeline",
    "build_tester_prompt",
    "compile_architect_resume_pipeline",
    "compile_coder_only_pipeline",
    "compile_pipeline",
    "compile_release_pipeline",
    "compile_spec_resume_pipeline",
    "resume_entry_for",
    "settled_threshold_terms",
]
