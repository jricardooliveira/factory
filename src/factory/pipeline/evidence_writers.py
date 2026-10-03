"""Best-effort evidence writers the nodes call: the artifact chain, the ADR and the
trust package — each committed to the product repo the moment it is written.

Not a node module (see `pipeline.nodes`): a sibling of `agent_calls`, the other
side-effect helper every node shares.

Evidence must never be able to fail a run, so the writers swallow I/O errors —
the chain writer returns None so the caller can record the gap.

The project directory IS the product's git repository, so every piece of
evidence is committed as it is produced (subject prefix ``factory:``). Left
uncommitted, the coder's governance check would see it as an undeclared write,
and the next task's checkpoint commit would bury it inside a code commit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from factory.evidence import artifacts
from factory.pipeline.state import PipelineState
from factory.workspace.git import git_commit_paths


def commit_evidence(state: PipelineState, path: Path | str | None, what: str) -> None:
    """Commit one evidence file to the project repo (best-effort, never raises)."""
    project_dir = state.get("project_dir")
    if not project_dir or not path:
        return
    try:
        git_commit_paths(Path(project_dir), [Path(path)], f"factory: {what}")
    except Exception:
        pass  # evidence is best-effort; a failed commit must not fail the run


def write_chain_artifact(
    state: PipelineState, kind: str, *args: Any, **kwargs: Any
) -> str | None:
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
            "release": artifacts.write_release_notes,
        }[kind]
        path = writer(Path(project_dir), state["story_id"], *args, **kwargs)
    except (OSError, KeyError, ValueError):
        return None
    if not path:
        return None
    commit_evidence(state, path, f"{state['story_id']} {kind.upper()}")
    return str(path)


def commit_adr(state: PipelineState, adr_path: Path | str) -> None:
    """Commit the ADR the architect node just wrote."""
    commit_evidence(state, adr_path, f"{state['story_id']} ADR")


def write_trust_package(state: PipelineState) -> Path | None:
    """Best-effort: save the assembled trust package to docs/releases/ (project runs).

    Returns where it was saved, or None — never raises. gate-release turns a None
    on a project run into a named gap: an unsaved package is not release evidence.
    """
    project_dir = state.get("project_dir")
    if not project_dir:
        return None
    try:
        from factory.evidence import trust_package

        pkg = trust_package.assemble(Path(state["db_path"]), state["run_id"])
        releases = Path(project_dir) / "docs" / "releases"
        releases.mkdir(parents=True, exist_ok=True)
        target = releases / f"run-{state['run_id']}-trust-package.json"
        target.write_text(json.dumps(pkg, indent=2), encoding="utf-8")
    except Exception:
        return None  # never let release-note I/O fail the run
    commit_evidence(state, target, f"{state['story_id']} trust package (run {state['run_id']})")
    return target


def release_evidence_gaps(state: PipelineState) -> list[str]:
    """The trust package's named blockers for this run — what release must not paper over.

    Never raises: if the package cannot be assembled, THAT is the gap.
    """
    try:
        from factory.evidence import trust_package

        pkg = trust_package.assemble(Path(state["db_path"]), state["run_id"])
    except Exception as e:  # noqa: BLE001 — the failure is reported as a gap
        return [f"The trust package could not be assembled: {e}"]
    return list(pkg.get("blockers") or []) + [
        f"The trust package does not match its schema: {e}"
        for e in trust_package.schema_errors(pkg)
    ]
