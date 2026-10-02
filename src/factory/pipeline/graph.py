"""The LangGraph wiring: conditional edges, resume routing, and every graph builder.

spec-agent → gate-1 → architect-agent → gate-2 → coder-agent (one task per pass)
→ tester-agent → gate-test, with bounded loops back for retries, re-architecture
and tester-driven remediation. The resume graphs re-enter that line at a parked
checkpoint.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from factory.pipeline.boss import authorized
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
    # `blocked` = the boss refused the architect: the line stops here too.
    if state.get("status") in ("failed", "blocked", "waiting_human"):
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

# Every agent stage runs behind the boss (pipeline.boss): authorized from the
# recorded verdicts before it starts. The spec-agent is the line's entry — its
# input is the operator's request itself, so there is nothing to authorize.
_AGENT_NODES = {
    "architect-agent": node_architect_agent,
    "coder-agent": node_coder_agent,
    "tester-agent": node_tester_agent,
}


def build_pipeline(entry: str = "spec-agent") -> StateGraph:
    """The whole line, entered at `entry` (a resume re-enters mid-line).

    One graph for every entry point: the resume graphs used to be four
    hand-copied subsets of this wiring, and every copy had to be kept in step.
    Nodes before the entry are simply never reached.
    """
    graph = StateGraph(PipelineState)
    graph.add_node("spec-agent", node_spec_agent)
    graph.add_node("gate-1", node_gate_1)
    graph.add_node("gate-2", node_gate_2)
    graph.add_node("gate-test", node_gate_test)
    for stage, node in _AGENT_NODES.items():
        graph.add_node(stage, authorized(stage, node))

    graph.set_entry_point(entry)
    graph.add_conditional_edges("spec-agent", should_continue_after_spec)
    graph.add_conditional_edges("gate-1", should_continue_after_gate_1)
    graph.add_edge("architect-agent", "gate-2")
    graph.add_conditional_edges("gate-2", should_continue_after_gate_2)
    graph.add_conditional_edges("coder-agent", route_after_coder)
    graph.add_edge("tester-agent", "gate-test")
    graph.add_conditional_edges("gate-test", route_after_gate_test)
    return graph


def compile_pipeline():
    return build_pipeline().compile()


def build_coder_only_pipeline() -> StateGraph:
    """Entered at the coder (resume after an approved design). The coder's
    infeasible-design feedback can still route back to the architect."""
    return build_pipeline(entry="coder-agent")


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
    return build_pipeline(entry="spec-agent")


def compile_spec_resume_pipeline():
    return build_spec_resume_pipeline().compile()


def build_architect_resume_pipeline() -> StateGraph:
    """Re-entry from architecture: used when a human REJECTS the architecture
    checkpoint (or approves Checkpoint 1). Re-runs the architect → gate-2 → coder.
    """
    return build_pipeline(entry="architect-agent")


def compile_architect_resume_pipeline():
    return build_architect_resume_pipeline().compile()
