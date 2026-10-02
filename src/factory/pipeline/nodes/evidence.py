"""Best-effort evidence writers the nodes call: the artifact chain and the trust package.

Evidence must never be able to fail a run, so both writers swallow I/O errors —
the chain writer returns None so the caller can record the gap.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from factory.evidence import artifacts
from factory.pipeline.state import PipelineState


def _write_chain_artifact(state: PipelineState, kind: str, *args: Any) -> str | None:
    """Write one link of the committed artifact chain (best-effort).

    Evidence must never be able to fail a run — but a silent `except: pass` is how
    the trust-package writer stayed invisible across 16 runs, so the failure is
    returned for the caller to record rather than swallowed.
    """
    project_dir = state.get("project_dir")
    if not project_dir:
        return None
    try:
        writer = {
            "intent": artifacts.write_intent,
            "spec": artifacts.write_spec,
            "plan": artifacts.write_plan,
        }[kind]
        path = writer(Path(project_dir), state["story_id"], *args)
        return str(path) if path else None
    except (OSError, KeyError, ValueError):
        return None


def _write_trust_package(state: PipelineState) -> None:
    """Best-effort: save the assembled trust package to docs/releases/ (project runs)."""
    project_dir = state.get("project_dir")
    if not project_dir:
        return
    try:
        from factory.evidence import trust_package

        pkg = trust_package.assemble(Path(state["db_path"]), state["run_id"])
        releases = Path(project_dir) / "docs" / "releases"
        releases.mkdir(parents=True, exist_ok=True)
        (releases / f"run-{state['run_id']}-trust-package.json").write_text(
            json.dumps(pkg, indent=2), encoding="utf-8"
        )
    except Exception:
        pass  # never let release-note I/O fail the run
