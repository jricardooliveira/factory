"""Continuous evals: regression testing for the AGENT CONFIGURATION.

The AI-Native SDLC playbook's Stage-4 play that this factory was missing entirely.
Its point is that in an AI-native pipeline the *configuration* — agent definitions,
gate policy, tier policy, prompt assembly — is production behaviour. Editing
`coder-agent.md` changes what the factory builds as surely as editing `pipeline/`
does, and nothing here regression-tested that.

Two kinds of eval, both offline and free (no opencode calls, zero tokens):

**config** — deterministic invariants over `agents/*.md` and the
registries they must agree with. These catch the silent, catastrophic drifts:
an agent whose write tool got re-enabled (which bypasses `materialize` and the
out-of-band-write gate — the whole governance model), a `model_tier` that no
longer matches `agent_config.tiers.AGENT_TIERS`, or a JSON contract in the prompt that
has drifted from the Pydantic model the orchestrator validates against (fields
the model doesn't know are silently dropped, so the agent obeys an instruction
the code ignores).

**replay** — behavioural cases: a story's frozen agent outputs driven through the
REAL pipeline, asserting the final status and per-gate verdicts. Because every
run stores its agents' verbatim output, `capture_case()` turns any real run — and
per the playbook, any incident — into a permanent case at zero cost. This is the
"each production incident becomes a permanent eval" rule, made affordable.

`make evals` gates on `EvalReport.passed` (100% by default), so a configuration
change that breaks the contract cannot land quietly. An EMPTY suite never passes:
a vacuous gate is how an eval suite rots into decoration.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from factory.agent_config.tiers import AGENT_TIERS, TIER_DEFAULTS
from factory.domain.contracts import (
    ArchitectOutput,
    CodeBlock,
    CoderOutput,
    SpecOutput,
    TaskDef,
    TesterOutput,
    TestCoverage,
)

# The pass-rate the suite must hit for `make evals` / CI to go green. The playbook
# calls for "a pass-rate threshold gated as a merge check"; for a suite this small
# and this deterministic, anything below 100% means a real regression.
PASS_THRESHOLD = 1.0

# Tools that MUST stay disabled in every agent definition. Code reaches disk only
# through the coder's declared `code_blocks` → `materialize_code_blocks`; an agent
# that can write, edit, patch or shell out bypasses that path and the out-of-band
# write detection in `gate-build` along with it.
FORBIDDEN_TOOLS = ("write", "edit", "bash", "patch")

# agent → the Pydantic model the orchestrator validates its output against.
OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    "spec-agent": SpecOutput,
    "architect-agent": ArchitectOutput,
    "coder-agent": CoderOutput,
    "tester-agent": TesterOutput,
}

# Nested models, so a drifted key inside a list/object is caught too.
NESTED_MODELS: dict[str, type[BaseModel]] = {
    "tasks": TaskDef,
    "code_blocks": CodeBlock,
    "test_coverage": TestCoverage,
}

_FENCE_RE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)


# ── Result / report types ─────────────────────────────────────────

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


# ── Paths ─────────────────────────────────────────────────────────

def _repo_root() -> Path:
    # src/factory/selftest/evals.py -> the factory repo root.
    return Path(__file__).resolve().parents[3]


def default_agents_dir() -> Path:
    # The real directory, not the `.opencode/agents` symlink opencode resolves.
    return _repo_root() / "agents"


def default_cases_dir() -> Path:
    return _repo_root() / "evals" / "cases"


# ── Config checks ─────────────────────────────────────────────────

def _frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    return yaml.safe_load(parts[1]) or {}


def _json_example(text: str) -> dict[str, Any] | None:
    """The first fenced block in an agent definition that is a JSON object.

    This block is the output contract the model actually sees, so it is the thing
    that must agree with the Pydantic model — not the prose around it.
    """
    for block in _FENCE_RE.findall(text):
        stripped = block.strip()
        if not stripped.startswith("{"):
            continue
        try:
            parsed = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _unknown_keys(example: dict[str, Any], model: type[BaseModel]) -> list[str]:
    """Keys in the prompt's contract that the model would silently discard."""
    known = set(model.model_fields)
    unknown = [k for k in example if k not in known]
    for key, nested in NESTED_MODELS.items():
        if key not in example or key not in known:
            continue
        value = example[key]
        items = value if isinstance(value, list) else [value]
        nested_known = set(nested.model_fields)
        for item in items:
            if isinstance(item, dict):
                unknown += [f"{key}.{k}" for k in item if k not in nested_known]
    return unknown


def _missing_required(example: dict[str, Any], model: type[BaseModel]) -> list[str]:
    return [
        name
        for name, f in model.model_fields.items()
        if f.is_required() and name not in example
    ]


def _check(name: str, passed: bool, detail: str = "") -> EvalResult:
    return EvalResult(name=name, kind="config", passed=passed, detail=detail)


def config_checks(*, agents_dir: Path | None = None) -> list[EvalResult]:
    """Deterministic invariants over the agent configuration. No LLM, no I/O cost."""
    agents_dir = agents_dir or default_agents_dir()
    results: list[EvalResult] = []

    for agent, tier in AGENT_TIERS.items():
        path = agents_dir / f"{agent}.md"
        if not path.is_file():
            results.append(
                _check(f"agent-definition-exists:{agent}", False, f"missing {path}")
            )
            continue
        results.append(_check(f"agent-definition-exists:{agent}", True))
        text = path.read_text(encoding="utf-8")
        fm = _frontmatter(text)

        # ── Governance: the tools that would bypass materialize stay off ──
        tools = fm.get("tools") or {}
        enabled = [t for t in FORBIDDEN_TOOLS if tools.get(t) is not False]
        results.append(
            _check(
                f"agent-tools-disabled:{agent}",
                not enabled,
                "" if not enabled
                else (
                    f"tools not explicitly disabled: {enabled}. An agent that can "
                    f"{'/'.join(enabled)} writes outside code_blocks, bypassing "
                    f"materialize and the out-of-band-write check in gate-build."
                ),
            )
        )

        # ── Tier policy declared in the file matches the registry ──
        results.append(
            _check(
                f"agent-tier-matches-registry:{agent}",
                fm.get("model_tier") == tier,
                "" if fm.get("model_tier") == tier
                else f"declares model_tier={fm.get('model_tier')!r}, registry says {tier!r}",
            )
        )
        expected_model = TIER_DEFAULTS[tier]
        results.append(
            _check(
                f"agent-model-matches-tier:{agent}",
                fm.get("model") == expected_model,
                "" if fm.get("model") == expected_model
                else f"declares model={fm.get('model')!r}, tier {tier!r} default is {expected_model!r}",
            )
        )

        # ── The prompt must demand JSON-only: run_agent_json depends on it ──
        results.append(
            _check(
                f"agent-demands-json-only:{agent}",
                "only a json" in text.lower(),
                "" if "only a json" in text.lower()
                else "no JSON-only instruction; the orchestrator parses the reply as JSON",
            )
        )

        # ── Output contract in the prompt vs the model the code validates with ──
        model = OUTPUT_MODELS.get(agent)
        if model is None:
            results.append(
                _check(
                    f"agent-output-contract:{agent}",
                    False,
                    f"no Pydantic output model registered for {agent} in OUTPUT_MODELS",
                )
            )
            continue
        example = _json_example(text)
        if example is None:
            results.append(
                _check(
                    f"agent-output-contract:{agent}",
                    False,
                    "no JSON output example found in the definition",
                )
            )
            continue
        problems: list[str] = []
        unknown = _unknown_keys(example, model)
        if unknown:
            problems.append(
                f"contract declares fields {model.__name__} does not have (silently "
                f"dropped by the orchestrator): {unknown}"
            )
        missing = _missing_required(example, model)
        if missing:
            problems.append(f"contract omits required {model.__name__} fields: {missing}")
        try:
            model.model_validate(example)
        except ValidationError as exc:
            problems.append(f"contract does not validate against {model.__name__}: {exc}")
        results.append(
            _check(f"agent-output-contract:{agent}", not problems, "; ".join(problems))
        )

    # ── The versioned review policy is agent configuration too ──
    results.extend(_review_policy_checks())
    return results


def _review_policy_checks() -> list[EvalResult]:
    """The review policy is injected into the tester's prompt, so it is part of the
    agent configuration and drifts the same way an agent definition does."""
    from factory.agent_config import review_policy

    text = review_policy.load_review_policy()
    if not text:
        return [
            _check(
                "review-policy-exists",
                False,
                f"no review policy at {review_policy.default_policy_path()} — the "
                f"tester falls back to its own prose and the policy is untunable",
            )
        ]
    results = [_check("review-policy-exists", True)]
    missing = [
        v for v in ("qa_verdict", "security_verdict", "performance_verdict")
        if v not in text
    ]
    results.append(
        _check(
            "review-policy-covers-sub-verdicts",
            not missing,
            "" if not missing
            else f"no review pass maps to {missing}; the tester would be guessing",
        )
    )
    for token, label in (("What to skip", "skip-list"), ("Nit cap", "nit-cap")):
        results.append(
            _check(
                f"review-policy-declares-{label}",
                token in text,
                "" if token in text
                else f"policy has no '{token}' section — findings volume is unbounded, "
                     f"which dilutes the blocking ones (trust per interruption)",
            )
        )
    return results


# ── Replay cases ──────────────────────────────────────────────────

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
    from factory.selftest.simulate import Scenario, run_scenario
    from factory.workspace.git import git_init

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


# ── Capture: turn a real run into a permanent case ────────────────

def capture_case(
    db_path: Path,
    run_id: int,
    name: str,
    *,
    description: str = "",
    cases_dir: Path | None = None,
    expect_status: str | None = None,
) -> Path:
    """Freeze a real run's agent outputs into a replayable eval case.

    This is what makes the playbook's "every production incident becomes a
    permanent eval" affordable: the outputs are already stored verbatim, so a
    case costs nothing to create and nothing to re-run.
    """
    from factory.state.db import get_db, get_run_logs

    cases_dir = cases_dir or default_cases_dir()
    with get_db(db_path) as conn:
        run = conn.execute(
            "SELECT id, status, story_id FROM pipeline_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if not run:
            raise ValueError(f"No run found with id #{run_id}")
        logs = get_run_logs(conn, run_id)

    outputs: dict[str, Any] = {}
    for log in logs:
        agent = log["agent"]
        text = log["output_text"] or ""
        if not text.strip():
            continue
        if agent == "coder-agent":
            # stage_type carries the task id (or "remediation") the row belongs to.
            slot = log["stage_type"] if log["stage_type"] not in (None, "agent") else "T-0001"
            outputs.setdefault(agent, {})[slot] = text
        else:
            # Later rows win: the last output is the one the run actually acted on.
            outputs[agent] = text

    if not outputs:
        raise ValueError(f"Run #{run_id} has no agent outputs to capture")

    payload = {
        "name": name,
        "description": description or f"captured from run #{run_id} ({run['story_id']})",
        "source_run_id": run_id,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git": True,
        "agent_outputs": outputs,
        "expect": {"status": expect_status or run["status"]},
    }
    cases_dir.mkdir(parents=True, exist_ok=True)
    path = cases_dir / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


# ── Suite ─────────────────────────────────────────────────────────

def run_all(
    *, agents_dir: Path | None = None, cases_dir: Path | None = None
) -> EvalReport:
    """The whole offline suite: configuration invariants + behavioural corpus."""
    results = config_checks(agents_dir=agents_dir)
    results += [run_case(c) for c in load_cases(cases_dir=cases_dir)]
    return EvalReport(results)


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
