"""The LangGraph wiring: conditional edges, resume routing, and every graph builder.

spec-agent → gate-1 → architect-agent → gate-2 → coder-agent (one task per pass)
→ tester-agent → gate-test, with bounded loops back for retries, re-architecture
and tester-driven remediation. The resume graphs re-enter that line at a parked
checkpoint.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from factory.pipeline.nodes.architect import node_architect_agent
from factory.pipeline.nodes.coder import node_coder_agent
from factory.pipeline.nodes.gates import node_gate_1, node_gate_2, node_gate_test
from factory.pipeline.nodes.spec import node_spec_agent
from factory.pipeline.nodes.tester import node_tester_agent
from factory.pipeline.state import PipelineState

# ── Conditional edges ─────────────────────────────────────────────


def should_continue_after_gate_1(state: PipelineState) -> str:
    # `waiting_human` is Checkpoint 1: the line stops until the operator answers.
    if state.get("status") in ("failed", "blocked", "waiting_human"):
        return END
    return "architect-agent"


def should_continue_after_gate_2(state: PipelineState) -> str:
    if state.get("status") in ("failed", "waiting_human"):
        return END
    return "coder-agent"


def route_after_coder(state: PipelineState) -> str:
    """Loop back for the next task / retry, re-architect on infeasible-design
    feedback, hand a completed story to the tester, else end."""
    action = state.get("next_action")
    if action in ("retry", "next_task"):
        return "coder-agent"
    if action == "rearchitect":
        return "architect-agent"
    if action == "complete":
        return "tester-agent"
    return END


def should_continue_after_spec(state: PipelineState) -> str:
    if state.get("status") in ("failed", "blocked"):
        return END
    return "gate-1"


def route_after_gate_test(state: PipelineState) -> str:
    """Loop back to the coder for a tester-driven remediation pass, else end."""
    if state.get("remediation") and state.get("status") not in ("failed", "blocked", "completed"):
        return "coder-agent"
    return END


# ── Build the graph ───────────────────────────────────────────────


def _wire_tester(graph: StateGraph) -> None:
    """Attach the post-implementation tester gate (coder 'complete' routes here)."""
    graph.add_node("tester-agent", node_tester_agent)
    graph.add_node("gate-test", node_gate_test)
    graph.add_edge("tester-agent", "gate-test")
    graph.add_conditional_edges("gate-test", route_after_gate_test)


def build_pipeline() -> StateGraph:
    graph = StateGraph(PipelineState)

    graph.add_node("spec-agent", node_spec_agent)
    graph.add_node("gate-1", node_gate_1)
    graph.add_node("architect-agent", node_architect_agent)
    graph.add_node("gate-2", node_gate_2)
    graph.add_node("coder-agent", node_coder_agent)

    graph.set_entry_point("spec-agent")

    graph.add_conditional_edges("spec-agent", should_continue_after_spec)
    graph.add_conditional_edges("gate-1", should_continue_after_gate_1)
    graph.add_conditional_edges("gate-2", should_continue_after_gate_2)
    graph.add_edge("architect-agent", "gate-2")
    graph.add_conditional_edges("coder-agent", route_after_coder)
    _wire_tester(graph)

    return graph


def compile_pipeline():
    return build_pipeline().compile()


def build_coder_only_pipeline() -> StateGraph:
    """Mini-pipeline entered at the coder (resume after human approval). Includes
    the architect + gate-2 nodes so the coder's infeasible-design feedback can
    still route to a re-architecture; they're never entered on the happy path."""
    graph = StateGraph(PipelineState)
    graph.add_node("coder-agent", node_coder_agent)
    graph.add_node("architect-agent", node_architect_agent)
    graph.add_node("gate-2", node_gate_2)
    graph.set_entry_point("coder-agent")
    graph.add_conditional_edges("coder-agent", route_after_coder)
    graph.add_edge("architect-agent", "gate-2")
    graph.add_conditional_edges("gate-2", should_continue_after_gate_2)
    _wire_tester(graph)
    return graph


def compile_coder_only_pipeline():
    return build_coder_only_pipeline().compile()


def resume_entry_for(gate_name: str | None, action: str) -> str:
    """Which stage a parked run re-enters, given the gate that parked it.

    Keeps the routing decision out of the CLI's I/O so it is testable offline.

    - Checkpoint 1 (gate-1-spec): approve accepts the story as written and moves
      to design; reject means the operator ANSWERED the open questions, so the
      story itself must be rewritten — re-enter at the spec-agent.
    - Checkpoint 2 (gate-2-architect): approve continues to implementation;
      reject re-runs the design with the feedback. (Pre-existing behaviour, and
      the fallback for any gate we don't recognize.)
    """
    if gate_name == "gate-1-spec":
        return "architect" if action == "approve" else "spec"
    return "coder" if action == "approve" else "architect"


def build_spec_resume_pipeline() -> StateGraph:
    """Re-entry from the story itself: used when a human REJECTS Checkpoint 1.
    Re-runs the spec-agent (carrying the operator's answers as `prior_findings`)
    and then flows on through the normal line."""
    graph = StateGraph(PipelineState)
    graph.add_node("spec-agent", node_spec_agent)
    graph.add_node("gate-1", node_gate_1)
    graph.add_node("architect-agent", node_architect_agent)
    graph.add_node("gate-2", node_gate_2)
    graph.add_node("coder-agent", node_coder_agent)
    graph.set_entry_point("spec-agent")
    graph.add_conditional_edges("spec-agent", should_continue_after_spec)
    graph.add_conditional_edges("gate-1", should_continue_after_gate_1)
    graph.add_edge("architect-agent", "gate-2")
    graph.add_conditional_edges("gate-2", should_continue_after_gate_2)
    graph.add_conditional_edges("coder-agent", route_after_coder)
    _wire_tester(graph)
    return graph


def compile_spec_resume_pipeline():
    return build_spec_resume_pipeline().compile()


def build_architect_resume_pipeline() -> StateGraph:
    """Re-entry from architecture: used when a human REJECTS the architecture
    checkpoint. Re-runs the architect (with reviewer feedback) → gate-2 → coder.
    """
    graph = StateGraph(PipelineState)
    graph.add_node("architect-agent", node_architect_agent)
    graph.add_node("gate-2", node_gate_2)
    graph.add_node("coder-agent", node_coder_agent)
    graph.set_entry_point("architect-agent")
    graph.add_edge("architect-agent", "gate-2")
    graph.add_conditional_edges("gate-2", should_continue_after_gate_2)
    graph.add_conditional_edges("coder-agent", route_after_coder)
    _wire_tester(graph)
    return graph


def compile_architect_resume_pipeline():
    return build_architect_resume_pipeline().compile()
