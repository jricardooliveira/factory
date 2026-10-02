"""Config evals: deterministic invariants over `agents/*.md` and the registries
they must agree with (tiers, output models, the review policy)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from factory.agent_config import tiers
from factory.agent_config.location import agents_dir as _agents_dir
from factory.agent_config.location import checkout_root
from factory.domain.contracts import (
    ArchitectOutput,
    CodeBlock,
    CoderOutput,
    SpecOutput,
    TaskDef,
    TesterOutput,
    TestCoverage,
)
from factory.selftest.evals.report import EvalResult

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




def default_agents_dir() -> Path:
    # The real directory, not the `.opencode/agents` links opencode resolves.
    return _agents_dir()


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


def default_opencode_dir() -> Path:
    """The checkout's ``.opencode`` — what every product's ``.opencode`` links to."""
    return checkout_root() / ".opencode"


def _opencode_loaded_agents(opencode_dir: Path) -> dict[str, Path]:
    """agent name -> file, for every agent opencode would load from `opencode_dir`.

    Mirrors opencode's own scan: ``{agent,agents}/**/*.md`` with symlinks FOLLOWED,
    the agent named by its path relative to that folder minus ``.md`` (so
    ``agents/policies/REVIEW.md`` is an agent called ``policies/REVIEW``).
    """
    loaded: dict[str, Path] = {}
    for folder in ("agent", "agents"):
        base = opencode_dir / folder
        if not base.exists():
            continue
        for dirpath, _dirs, files in os.walk(base, followlinks=True):
            for name in files:
                if name.endswith(".md"):
                    path = Path(dirpath) / name
                    loaded[path.relative_to(base).with_suffix("").as_posix()] = path
    return loaded


def _opencode_agents_check(
    agents_dir: Path, opencode_dir: Path, expected: set[str]
) -> EvalResult:
    """opencode must load exactly the registered agents, each from `agents_dir`.

    A stray markdown file in the scanned tree becomes an extra agent with
    opencode's DEFAULT permissions (write/edit/bash enabled); a missing link means
    the factory calls an agent opencode cannot find.
    """
    loaded = _opencode_loaded_agents(opencode_dir)
    problems: list[str] = []
    extra = sorted(set(loaded) - expected)
    missing = sorted(expected - set(loaded))
    if extra:
        problems.append(
            f"opencode would also load {extra} as agents, with default (write-enabled) "
            f"permissions — keep only agent definitions under {opencode_dir}/agents"
        )
    if missing:
        problems.append(f"opencode cannot find {missing} under {opencode_dir}/agents")
    for name in sorted(expected & set(loaded)):
        target = (agents_dir / f"{name}.md").resolve()
        if loaded[name].resolve() != target:
            problems.append(f"{name} resolves to {loaded[name].resolve()}, not {target}")
    return _check("opencode-loads-exactly-the-agents", not problems, "; ".join(problems))


def config_checks(
    *, agents_dir: Path | None = None, opencode_dir: Path | None = None
) -> list[EvalResult]:
    """Deterministic invariants over the agent configuration. No LLM, no I/O cost."""
    agents_dir = agents_dir or default_agents_dir()
    opencode_dir = opencode_dir or default_opencode_dir()
    tier_config = tiers.config()
    results: list[EvalResult] = []

    for agent, tier in tier_config.agent_tiers.items():
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
        expected_model = tier_config.tier_models[tier]
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

    # ── What opencode actually loads: exactly these agents, nothing else ──
    results.append(
        _opencode_agents_check(agents_dir, opencode_dir, set(tier_config.agent_tiers))
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
