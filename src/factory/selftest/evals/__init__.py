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

Layout: `config` (invariants), `cases` (load + replay), `capture`, `report`
(result types, threshold, markdown).

`make evals` gates on `EvalReport.passed` (100% by default), so a configuration
change that breaks the contract cannot land quietly. An EMPTY suite never passes:
a vacuous gate is how an eval suite rots into decoration.
"""

from __future__ import annotations

from pathlib import Path

from factory.selftest.evals.capture import capture_case
from factory.selftest.evals.cases import default_cases_dir, load_cases, run_case
from factory.selftest.evals.config import (
    FORBIDDEN_TOOLS,
    NESTED_MODELS,
    OUTPUT_MODELS,
    config_checks,
    default_agents_dir,
)
from factory.selftest.evals.report import (
    PASS_THRESHOLD,
    EvalCase,
    EvalReport,
    EvalResult,
    render_markdown,
)

__all__ = [
    "FORBIDDEN_TOOLS",
    "NESTED_MODELS",
    "OUTPUT_MODELS",
    "PASS_THRESHOLD",
    "EvalCase",
    "EvalReport",
    "EvalResult",
    "capture_case",
    "config_checks",
    "default_agents_dir",
    "default_cases_dir",
    "load_cases",
    "render_markdown",
    "run_all",
    "run_case",
]


def run_all(
    *, agents_dir: Path | None = None, cases_dir: Path | None = None
) -> EvalReport:
    """The whole offline suite: configuration invariants + behavioural corpus."""
    results = config_checks(agents_dir=agents_dir)
    results += [run_case(c) for c in load_cases(cases_dir=cases_dir)]
    return EvalReport(results)
