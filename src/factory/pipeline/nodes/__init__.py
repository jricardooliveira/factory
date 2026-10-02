"""LangGraph nodes: one module per agent stage, plus the deterministic gate nodes.

Each node takes the PipelineState and returns a partial update. Every terminal
outcome a node decides is persisted with `finish_run` before it returns.
"""
