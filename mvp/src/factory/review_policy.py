"""Load the versioned review policy the tester reviews against.

The playbook's Stage-5 play separates the reviewer's *role* from the *policy* it
applies: the agent definition says what the tester is and what shape its output
takes; `docs/factory/REVIEW.md` says what to look for, how severe it is, and what
to ignore. Keeping them apart means the operator tunes review behaviour by editing
one committed markdown file — no agent definition edit, no code change — and the
policy version is visible in git history.

Before this, the review passes were prose inside `tester-agent.md` while the
blocking severity threshold was a branch in `gates.py`: one policy, two homes,
free to drift silently.

Editing REVIEW.md is an agent-configuration change: `make evals` is the net.
"""

from __future__ import annotations

from pathlib import Path

_DEFAULT_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "factory" / "REVIEW.md"
)


def default_policy_path() -> Path:
    return _DEFAULT_PATH


def load_review_policy(path: Path | None = None) -> str:
    """The policy text, or "" when there is none.

    Degrades to empty rather than raising: a missing policy must leave the tester
    working off its own definition, not break the run.
    """
    target = path or _DEFAULT_PATH
    try:
        return target.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def policy_block(path: Path | None = None) -> str:
    """The policy as a labelled prompt section ('' when there is no policy)."""
    text = load_review_policy(path)
    if not text:
        return ""
    return (
        "## Review policy (MUST follow — this is the versioned policy you are "
        "reviewing against)\n\n"
        f"{text}\n\n"
    )
