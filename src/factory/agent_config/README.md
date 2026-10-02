# factory.agent_config

## Responsibility

The code side of the agent configuration. The configuration itself (the six agent `.md`
definitions, `policies/REVIEW.md` and `tiers.toml`) is data in the repo-root `agents/` directory;
this package locates it, loads and validates it, and answers "which model does this agent call
use?" and "what is the review policy?". `agents/tiers.toml` is the only place model ids are
chosen.

## Modules

| Module | What it does |
|---|---|
| `location.py` | `agents_dir()` resolves the config directory; `checkout_root()` is the repo root (holds `agents/`, `.opencode/`, `evals/`, `examples/`; only meaningful in a source checkout). Constants `AGENTS_DIR_ENV`, `TIERS_FILENAME`. |
| `tiers.py` | `load_tiers` parses and validates `tiers.toml` into a frozen `TierConfig`; `config()` caches the default one. `model_for_tier`, `tier_for_agent`, `resolve_model` -> `(model_id, tier)`, `distinct_models()` (model -> tiers it backs, one preflight probe per model). |
| `review_policy.py` | `load_review_policy` reads `policies/REVIEW.md` ("" if unreadable); `policy_block` wraps it as a labelled prompt section; `default_policy_path`. |

## How it works

- **Directory resolution** (`agents_dir`): `FACTORY_AGENTS_DIR` wins; else the checkout's `agents/`
  if it has `tiers.toml` (or if no packaged copy exists); else `factory/_agents` inside an
  installed wheel (force-included by `pyproject.toml`).
- **Tiers are loaded lazily.** `tiers.AGENT_TIERS`, `TIER_DEFAULTS`, `ESCALATE_ON_RETRY` and
  `DEFAULT_TIER` are module attributes served by PEP 562 `__getattr__`, which calls the cached
  `config()`. Reason: an import-time load made a missing or invalid `tiers.toml` crash read-only
  verbs (`factory list`, `--help`) and `factory doctor`, the command meant to diagnose it.
- **Validation is strict and up front** (`load_tiers`): every tier model must look like
  `provider/model`; every tier named by `[agents]` / `[escalate_on_retry]` and `default_tier` must be
  defined. Raises `ValueError`, so a bad tier surfaces before any agent is paid for.
- **Resolution order**: `tier_for_agent(name, attempt)` returns the `escalate_on_retry` tier when
  `attempt > 1` and the agent is listed there, else `[agents]`, else `default_tier`.
  `model_for_tier` lets an env var `FACTORY_TIER_<TIER>` override the file for one run.
  Unknown tier -> `ValueError`.
- **Review policy degrades to empty** on `OSError`, so a missing policy leaves the tester on its own
  definition rather than breaking a run. The text is embedded in the tester prompt (golden-pinned).
- **Drift guard**: each `agents/<name>.md` frontmatter repeats `model_tier:` and the tier's model;
  `tests/agent_config/test_tiers.py` and `factory evals` fail if they disagree with `tiers.toml`.

## Dependencies

Allowed to import only `domain` (per `tests/integration/test_layout.py`); in practice it imports
nothing outside the package. Used by `pipeline` (`agent_calls` uses `resolve_model`;
`prompts/tester.py` uses `review_policy`), `preflight/doctor.py`, `workspace` (`checkout_root`),
`selftest/evals` and `interfaces/cli/selftest.py`.

## Gotchas

- Changing a model: edit `agents/tiers.toml` and the matching agent frontmatter together, then
  run `make evals`. Default models must be ones the local opencode login accepts.
- Editing `REVIEW.md`, `tiers.toml` or anything here is an agent-configuration change: run `make evals`.
- Code that needs a file under `checkout_root()` must cope with it being absent (wheel install).
