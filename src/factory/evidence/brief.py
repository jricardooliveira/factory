"""The product brief: what the operator said the product is, before any story.

Rendered deterministically from the recorded interview answers — no model call, so
the brief can never say more than the operator did. An answer the operator
delegated ("you decide") is repeated under Assumptions so it is not mistaken for
a decision. `BRIEF.md` existing in the project IS the approval.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from factory.domain.interview import OTHER_TITLE, REQUIRED_TOPICS, TOPIC_TITLES

BRIEF_RELPATH = "docs/work/BRIEF.md"
TRANSCRIPT_RELPATH = "docs/work/INTERVIEW.md"

ASSUMPTIONS_HEADING = "## Assumptions (NOT decided by the operator)"


def brief_path(project_dir: Path) -> Path:
    return Path(project_dir) / BRIEF_RELPATH


def _line(answer: dict[str, Any]) -> str:
    return f"- **{answer['question']}** {answer['answer']}"


def render_brief(project_name: str, answers: list[dict[str, Any]]) -> str:
    sections: dict[str, list[str]] = {}
    for a in answers:
        sections.setdefault(TOPIC_TITLES.get(a["topic"], OTHER_TITLE), []).append(_line(a))
    # Checklist order first, then the remaining known topics, "Other" last.
    order = [TOPIC_TITLES[t] for t in REQUIRED_TOPICS]
    order += [t for t in TOPIC_TITLES.values() if t not in order] + [OTHER_TITLE]

    parts = [f"# Product brief — {project_name}"]
    parts += [f"## {title}\n\n" + "\n".join(sections[title]) for title in order if title in sections]
    assumed = [_line(a) for a in answers if a.get("assumed")]
    parts.append(f"{ASSUMPTIONS_HEADING}\n\n" + "\n".join(assumed or ["- (none)"]))
    return "\n\n".join(parts) + "\n"


def render_transcript(project_name: str, answers: list[dict[str, Any]]) -> str:
    parts = [f"# Intake interview — {project_name}"]
    for n, a in enumerate(answers, 1):
        lines = [f"{n}. **[{a['topic']}] {a['question']}**"]
        if a.get("options"):
            lines.append(f"   - Options offered: {' / '.join(a['options'])}")
        mark = " (assumed — the operator said \"you decide\")" if a.get("assumed") else ""
        lines.append(f"   - Answer: {a['answer']}{mark}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts) + "\n"


def write_brief(project_dir: Path, project_name: str, answers: list[dict[str, Any]]) -> list[Path]:
    """Write BRIEF.md + INTERVIEW.md. Does not commit — the caller owns the commit."""
    project_dir = Path(project_dir)
    written: list[Path] = []
    for relpath, text in (
        (BRIEF_RELPATH, render_brief(project_name, answers)),
        (TRANSCRIPT_RELPATH, render_transcript(project_name, answers)),
    ):
        path = project_dir / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def load_brief(project_dir: Path | None) -> str:
    if project_dir is None:
        return ""
    path = brief_path(project_dir)
    return path.read_text(encoding="utf-8") if path.is_file() else ""
