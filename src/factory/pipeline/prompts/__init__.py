"""Prompt assembly for the pipeline's agents. Agent prompt TEXT is production behaviour.

One module per agent (spec, architect, coder, tester), the blocks they share
(`blocks`), and the per-task packs (`context_pack`). Every assembled prompt is
pinned byte-for-byte by `tests/pipeline/prompts/test_prompt_golden.py`.
"""
