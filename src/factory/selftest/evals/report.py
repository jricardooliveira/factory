"""Eval result types, the pass threshold, and the markdown report."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The pass-rate the suite must hit for `make evals` / CI to go green. The playbook
# calls for "a pass-rate threshold gated as a merge check"; for a suite this small
# and this deterministic, anything below 100% means a real regression.
PASS_THRESHOLD = 1.0


@dataclass
class EvalResult:
    name: str
    kind: str  # "config" | "replay"
    passed: bool
    detail: str = ""
    expected: str = ""
    actual: str = ""


@dataclass
class EvalReport:
    results: list[EvalResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.passed) / len(self.results)

    @property
    def failures(self) -> list[EvalResult]:
        return [r for r in self.results if not r.passed]

    @property
    def passed(self) -> bool:
        # An empty suite is a FAILURE, not a pass: a gate with nothing behind it
        # reports green forever and stops protecting anything.
        return bool(self.results) and self.pass_rate >= PASS_THRESHOLD


@dataclass
class EvalCase:
    """A behavioural case: frozen agent outputs + the outcome they must produce."""

    name: str
    description: str
    agent_outputs: dict[str, Any]
    expect_status: str
    expect_gates: dict[str, bool] = field(default_factory=dict)
    git: bool = False
    source_run_id: int | None = None
    path: Path | None = None


def render_markdown(report: EvalReport) -> str:
    pct = round(report.pass_rate * 100)
    verdict = "PASS" if report.passed else "FAIL"
    lines = [
        "# Agent Configuration Eval Report",
        "",
        f"**{verdict}** — {sum(1 for r in report.results if r.passed)}/{len(report.results)} "
        f"checks green ({pct}%). Threshold: {round(PASS_THRESHOLD * 100)}%.",
        "",
        "Offline and deterministic — no opencode calls, zero tokens.",
        "",
        "| Check | Kind | OK | Detail |",
        "|---|---|:--:|---|",
    ]
    for r in report.results:
        ok = "✅" if r.passed else "❌"
        detail = (r.detail or "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{r.name}` | {r.kind} | {ok} | {detail[:300]} |")
    if report.failures:
        lines += ["", "## Failures", ""]
        for r in report.failures:
            lines.append(f"- **{r.name}** ({r.kind}): {r.detail}")
    return "\n".join(lines) + "\n"
