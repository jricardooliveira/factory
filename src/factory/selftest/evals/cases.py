"""Replay evals: a case's frozen agent outputs driven through the real pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from factory.agent_config.location import checkout_root
from factory.selftest.evals.report import EvalCase, EvalResult
from factory.selftest.simulate import Scenario, run_scenario
from factory.workspace.git import git_init


def default_cases_dir() -> Path:
    return checkout_root() / "evals" / "cases"


def _as_output_text(value: Any) -> str:
    """Case files may hold an agent's output as raw text or as a JSON object."""
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _normalize_outputs(raw: dict[str, Any]) -> dict[str, Any]:
    """Case JSON → the shape `simulate` replay expects.

    Every agent's value is output TEXT, except `coder-agent`, which is always a
    map of task id → output text (the pipeline logs one coder row per task and
    replays by that slot).
    """
    out: dict[str, Any] = {}
    for agent, value in raw.items():
        if agent == "coder-agent":
            if not isinstance(value, dict):
                raise ValueError(
                    "coder-agent outputs must be a map of task id → output "
                    f"(got {type(value).__name__}); the pipeline replays per task slot"
                )
            out[agent] = {tid: _as_output_text(v) for tid, v in value.items()}
        else:
            out[agent] = _as_output_text(value)
    return out


def load_cases(*, cases_dir: Path | None = None) -> list[EvalCase]:
    """Load the behavioural corpus, sorted by filename for a stable report order."""
    cases_dir = cases_dir or default_cases_dir()
    if not cases_dir.is_dir():
        return []
    cases: list[EvalCase] = []
    for path in sorted(cases_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        expect = data.get("expect") or {}
        cases.append(
            EvalCase(
                name=data.get("name") or path.stem,
                description=data.get("description", ""),
                agent_outputs=_normalize_outputs(data.get("agent_outputs") or {}),
                expect_status=expect.get("status", ""),
                expect_gates=expect.get("gates") or {},
                git=bool(data.get("git")),
                source_run_id=data.get("source_run_id"),
                path=path,
            )
        )
    return cases


def run_case(case: EvalCase) -> EvalResult:
    """Drive one case's frozen outputs through the real pipeline and judge it."""
    scenario = Scenario(
        name=case.name,
        description=case.description,
        outputs=_normalize_outputs(case.agent_outputs),
        expected_status=case.expect_status,
        setup=git_init if case.git else None,
    )
    outcome = run_scenario(scenario)

    problems: list[str] = []
    if outcome.actual_status != case.expect_status:
        problems.append(
            f"status {outcome.actual_status!r} != expected {case.expect_status!r}"
            + (f" ({outcome.error})" if outcome.error else "")
        )
    actual_gates = dict(outcome.gates)
    for gate, want in case.expect_gates.items():
        if gate not in actual_gates:
            problems.append(f"gate {gate!r} never ran (ran: {sorted(actual_gates)})")
        elif actual_gates[gate] != want:
            problems.append(
                f"gate {gate!r} was {actual_gates[gate]}, expected {want}"
            )

    return EvalResult(
        name=case.name,
        kind="replay",
        passed=not problems,
        detail="; ".join(problems),
        expected=case.expect_status,
        actual=outcome.actual_status,
    )
