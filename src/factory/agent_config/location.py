"""Where the agent configuration lives: the checkout's ``agents/``, else the copy
shipped inside the package.

In a source checkout (editable install, the normal way the factory runs) the
configuration is the repo-root ``agents/`` directory, next to the code that reads
it. A built wheel has no checkout around it, so ``pyproject.toml`` force-includes
``agents/`` into the package as ``factory/_agents``; without that fallback every
verb — read-only ``factory list`` included — crashed at import on a missing
``agents/tiers.toml``. ``FACTORY_AGENTS_DIR`` overrides both.
"""

from __future__ import annotations

import os
from pathlib import Path

AGENTS_DIR_ENV = "FACTORY_AGENTS_DIR"
TIERS_FILENAME = "tiers.toml"

# src/factory/agent_config/location.py -> parents[3] is the checkout root.
_CHECKOUT_ROOT = Path(__file__).resolve().parents[3]
# factory/_agents inside an installed wheel (see [tool.hatch...force-include]).
_PACKAGED_AGENTS = Path(__file__).resolve().parents[1] / "_agents"
_PACKAGED_SKILLS = Path(__file__).resolve().parents[1] / "_skills"


def checkout_root() -> Path:
    """The factory checkout this code runs from: holds ``agents/``, ``.opencode/``,
    ``evals/`` and ``examples/``. Meaningful only in a source checkout — callers
    that need a file under it must cope with it being absent (a wheel install)."""
    return _CHECKOUT_ROOT


def agents_dir() -> Path:
    """The directory holding the agent definitions, ``tiers.toml`` and ``policies/``."""
    override = os.environ.get(AGENTS_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    checkout = _CHECKOUT_ROOT / "agents"
    if (checkout / TIERS_FILENAME).is_file() or not _PACKAGED_AGENTS.is_dir():
        return checkout
    return _PACKAGED_AGENTS


def product_skills_dir() -> Path:
    """The skills every product gets in ``.claude/skills/``: the checkout's ``skills/``,
    else the packaged copy (force-included like ``agents/``)."""
    checkout = _CHECKOUT_ROOT / "skills"
    return checkout if checkout.is_dir() or not _PACKAGED_SKILLS.is_dir() else _PACKAGED_SKILLS
