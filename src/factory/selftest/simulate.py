"""Offline scenario simulation.

Drives a catalog of representative stories through the REAL run service
(`runs.run_pipeline`, the path the CLI and TUI take) using the replay mechanism
(frozen agent outputs) — zero tokens, deterministic. Verifies
each scenario reaches its expected outcome and renders a "what works / what's
broken" report. Doubles as a living, executable spec of factory behavior.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from factory.domain.agent_output import parse_agent_json
from factory.evidence.progress import plain_flow, run_pipeline_progress
from factory.runs import run_pipeline
from factory.state import db
from factory.workspace.git import git_init


# ── tiny builders for canned agent outputs ───────────────────────────
def _task(tid: str, deps: list[str] | None = None) -> dict:
    return {"id": tid, "title": tid, "purpose": "do " + tid, "scope": [],
            "completion_evidence": "done", "depends_on": deps or []}


def _spec(title: str, tasks: list[dict], verdict: str = "pass",
          questions: list[str] | None = None) -> str:
    return json.dumps({
        "title": title, "problem": "p", "why": "w",
        "acceptance_criteria": ["ac one", "ac two"], "tasks": tasks,
        "verdict": verdict, "questions": questions or [],
    })


def _arch(modules: list[str], **extra: Any) -> str:
    d = {"verdict": "pass", "architecture_notes": "layered design",
         "modules_affected": modules}
    d.update(extra)
    return json.dumps(d)


def _coder(path: str, content: str, verdict: str = "complete") -> str:
    return json.dumps({"verdict": verdict,
                       "code_blocks": [{"path": path, "content": content, "action": "create"}]})


@dataclass
class Scenario:
    name: str
    description: str
    outputs: dict[str, Any]  # agent -> output text, or coder -> {task_id: text}
    expected_status: str
    setup: Callable[[Path], None] | None = None  # optional cwd prep (e.g. governance)


@dataclass
class ScenarioResult:
    scenario: Scenario
    actual_status: str
    gates: list[tuple[str, bool]] = field(default_factory=list)
    flow_text: str = ""
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.actual_status == self.scenario.expected_status


_TESTER_PASS = json.dumps({
    "overall": "pass", "qa_verdict": "pass", "ac_coverage": ["ac one", "ac two"],
    "security_verdict": "pass", "highest_severity": "none", "performance_verdict": "pass",
    "summary": "ok",
})


def _seed_original(conn, outputs: dict[str, Any]) -> int:
    """Create an original run with frozen agent outputs (coder per task slot)."""
    # Tester gate always runs on completion — default-seed a passing tester.
    if "tester-agent" not in outputs:
        outputs = {**outputs, "tester-agent": _TESTER_PASS}
    task_ids: list[str] = []
    spec = outputs.get("spec-agent")
    if isinstance(spec, str):
        parsed = parse_agent_json(spec) or {}
        task_ids = [t["id"] for t in parsed.get("tasks", [])]
    db.create_story(conn, "US-0001", "Sim", "do the thing")
    rid = db.start_run(conn, "US-0001")
    for agent, value in outputs.items():
        if agent == "coder-agent":
            per_task = value if isinstance(value, dict) else {(task_ids[0] if task_ids else "T-1"): value}
            for tid, out in per_task.items():
                db.log_agent(conn, rid, "coder-agent", "seed", out, verdict="complete", stage_type=tid)
        else:
            db.log_agent(conn, rid, agent, "seed", str(value), verdict="pass")
    return rid


def run_scenario(scenario: Scenario) -> ScenarioResult:
    """Replay one scenario through the run service — the same entry path the CLI
    and the TUI use (replay sandbox, base-commit pin, finish/outcome) — in a
    throwaway home. Nothing is notified: no operator is waiting on a scenario."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db_path = root / "factory.db"
        db.init_db(db_path)
        try:
            with db.get_db(db_path) as conn:
                orig = _seed_original(conn, scenario.outputs)
            outcome = run_pipeline(
                "do the thing",
                db_path=db_path,
                replay_run_id=orig,
                notify_operator=False,
                prepare_workdir=scenario.setup,
            )
            with db.get_db(db_path) as conn:
                gates = [
                    (g["gate_name"], bool(g["passed"]))
                    for g in db.get_run_gates(conn, outcome.run_id)
                ]
                row = db.get_run(conn, outcome.run_id)
            status = outcome.final_state.get("status")
            actual = status or (row["status"] if row else "unknown")
            flow = plain_flow(run_pipeline_progress(db_path, outcome.run_id))
            return ScenarioResult(scenario, actual, gates, flow)
        except Exception as exc:  # a crash is itself a failed scenario
            return ScenarioResult(scenario, "ERROR", error=str(exc))


def _governance_setup(cwd: Path) -> None:
    git_init(cwd)
    (cwd / "sneaky.py").write_text("x = 1\n")  # undeclared, out-of-band file


CATALOG: list[Scenario] = [
    Scenario(
        "greenfield_clean", "Single-task greenfield, clean code",
        {"spec-agent": _spec("Converter", [_task("T-1")]),
         "architect-agent": _arch(["c.py"]),
         "coder-agent": {"T-1": _coder("c.py", "def f():\n    return 1\n")}},
        "completed",
    ),
    Scenario(
        "multi_task", "Two dependent tasks in a GIT repo, each its own coder call",
        {"spec-agent": _spec("Pkg", [_task("T-1"), _task("T-2", deps=["T-1"])]),
         "architect-agent": _arch(["a.py", "b.py"]),
         "coder-agent": {"T-1": _coder("a.py", "x = 1\n"), "T-2": _coder("b.py", "y = 2\n")}},
        "completed",
        # Git-backed so the per-task scope check runs against the real diff —
        # this mirrors live projects and guards the "task 2 blocks" regression.
        setup=git_init,
    ),
    Scenario(
        "vague_request", "Spec has open questions -> parks at Checkpoint 1 for the operator",
        {"spec-agent": _spec("Vague", [_task("T-1")], questions=["which auth?"])},
        "waiting_human",
    ),
    Scenario(
        "malformed_spec", "Story with too few acceptance criteria -> rejected at gate-1",
        {"spec-agent": json.dumps({
            "title": "Thin", "problem": "p", "why": "w",
            "acceptance_criteria": ["only one"], "tasks": [_task("T-1")],
            "verdict": "pass", "questions": [],
        })},
        "failed",
    ),
    Scenario(
        "task_cycle", "Tasks depend on each other in a cycle -> rejected at gate-1",
        {"spec-agent": _spec("Cyclic", [_task("T-1", deps=["T-2"]), _task("T-2", deps=["T-1"])])},
        "failed",
    ),
    Scenario(
        "sensitive_work", "Architect flags sensitivity/external dep -> parks at gate-2",
        {"spec-agent": _spec("Billing", [_task("T-1")]),
         "architect-agent": _arch(["pay.py"], sensitivity=["pii", "financial"],
                                  external_dependencies=["Stripe"])},
        "waiting_human",
    ),
    Scenario(
        "broken_code", "Coder writes code that doesn't compile -> remediation exhausts -> failed",
        {"spec-agent": _spec("Broken", [_task("T-1")]),
         "architect-agent": _arch(["c.py"]),
         "coder-agent": {"T-1": _coder("c.py", "def f(:\n  pass\n")}},
        "failed",
    ),
    Scenario(
        "off_script_coder", "Coder returns non-JSON -> blocked",
        {"spec-agent": _spec("OffScript", [_task("T-1")]),
         "architect-agent": _arch(["c.py"]),
         "coder-agent": {"T-1": "I cannot help with that. Here is some prose."}},
        "blocked",
    ),
    Scenario(
        "tester_fail", "Tester finds an uncovered acceptance criterion -> gate-test fails",
        {"spec-agent": _spec("Tested", [_task("T-1")]),
         "architect-agent": _arch(["c.py"]),
         "coder-agent": {"T-1": _coder("c.py", "def f():\n    return 1\n")},
         "tester-agent": json.dumps({"overall": "fail", "qa_verdict": "fail",
                                     "missing_coverage": ["the search criterion"],
                                     "summary": "missing test"})},
        "failed",
    ),
    Scenario(
        "governance_violation", "Agent writes an undeclared file -> governance block",
        {"spec-agent": _spec("Govern", [_task("T-1")]),
         "architect-agent": _arch(["main.py"]),
         "coder-agent": {"T-1": _coder("main.py", "x = 1\n")}},
        "blocked",
        setup=_governance_setup,
    ),
]


def simulate_all() -> list[ScenarioResult]:
    return [run_scenario(s) for s in CATALOG]


def render_markdown(results: list[ScenarioResult]) -> str:
    passed = sum(1 for r in results if r.passed)
    lines = [
        "# Factory Simulation Report",
        "",
        f"**{passed}/{len(results)} scenarios behaving as expected** "
        "(offline, deterministic — no tokens spent).",
        "",
        "| Scenario | Expected | Actual | OK | Description |",
        "|---|---|---|:--:|---|",
    ]
    for r in results:
        ok = "✅" if r.passed else "❌"
        actual = r.actual_status + (f" ({r.error})" if r.error else "")
        lines.append(
            f"| `{r.scenario.name}` | {r.scenario.expected_status} | {actual} | {ok} "
            f"| {r.scenario.description} |"
        )
    lines.append("")
    lines.append("## Pipeline flow per scenario")
    lines.append("")
    for r in results:
        flow = r.flow_text or "(no flow)"
        lines.append(f"- **{r.scenario.name}**: {flow}")
    return "\n".join(lines) + "\n"
