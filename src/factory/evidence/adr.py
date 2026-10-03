"""Decision memory: persist architecture decisions as ADRs and feed prior
decisions back into agent prompts so the factory stays internally consistent
and stops re-litigating settled decisions.

See docs/contract/EFFECTIVENESS.md §7.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from factory.domain.contracts import ArchitectOutput
from factory.evidence.artifacts import stamp_status

# How many prior ADRs to feed back into a prompt (cost control).
_MAX_PRIOR_ADRS = 5


def _slug(text: str, maxlen: int = 50) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug[:maxlen].strip("-")) or "decision"


def adr_dir_for(project_dir: Path) -> Path:
    return project_dir / "docs" / "architecture" / "adr"


def _bullets(items: list[str], empty: str = "- none") -> str:
    return "\n".join(f"- {i}" for i in items) if items else empty


def render_adr(story_id: str, title: str, arch: ArchitectOutput) -> str:
    """Render an architecture decision as a Markdown ADR."""
    now = datetime.now(timezone.utc).date().isoformat()
    return "\n".join(
        [
            f"# ADR-{story_id}: {title}",
            "",
            f"- Date: {now}",
            f"- Story: {story_id}",
            "- Status: proposed (pending architecture sign-off)",
            "",
            "## Decision",
            arch.architecture_notes or "(none recorded)",
            "",
            "## Affected modules",
            _bullets(arch.modules_affected),
            "",
            "## Data / API impact",
            f"- DB impact: {arch.db_impact}",
            f"- API impact: {arch.api_impact}",
            f"- Migration needed: {arch.migration_needed}",
            "",
            "## Constraints for implementation",
            _bullets(arch.implementation_constraints),
            "",
            "## Risks",
            _bullets(arch.risks),
            "",
            "## Breaking changes",
            _bullets(arch.breaking_changes),
            "",
            "## Sensitivity",
            _bullets(arch.sensitivity),
            "",
        ]
    )


def write_adr(project_dir: Path, story_id: str, title: str, arch: ArchitectOutput) -> Path:
    """Persist an ADR for a story. Deterministic filename → replay-safe (overwrites)."""
    target_dir = adr_dir_for(project_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"ADR-{story_id}-{_slug(title)}.md"
    path.write_text(render_adr(story_id, title, arch), encoding="utf-8")
    return path


def stamp_adr(project_dir: Path, story_id: str, status: str) -> list[Path]:
    """Rewrite the Status of this story's ADR(s); returns the files stamped."""
    return [p for p in sorted(adr_dir_for(project_dir).glob(f"ADR-{story_id}-*.md"))
            if stamp_status(p, status)]


def load_project_memory(project_dir: Path, *, exclude_story: str | None = None) -> str:
    """Build a prompt block of PROJECT_RULES + prior ADRs for an agent to honor.

    `exclude_story` drops the current story's own ADR so an agent never feeds a
    prior attempt's decision back to itself as if it were settled.
    Returns "" when there is nothing to inject.
    """
    parts: list[str] = []

    rules = project_dir / "PROJECT_RULES.md"
    if rules.is_file():
        text = rules.read_text(encoding="utf-8").strip()
        if text:
            parts.append("## Project Rules (MUST follow)\n\n" + text)

    adr_dir = adr_dir_for(project_dir)
    if adr_dir.is_dir():
        adrs = sorted(adr_dir.glob("ADR-*.md"))
        if exclude_story:
            adrs = [p for p in adrs if f"ADR-{exclude_story}-" not in p.name]
        adrs = adrs[-_MAX_PRIOR_ADRS:]
        if adrs:
            rendered = "\n\n".join(p.read_text(encoding="utf-8").strip() for p in adrs)
            parts.append(
                "## Prior Architecture Decisions (stay consistent; do NOT re-litigate)\n\n"
                + rendered
            )

    return "\n\n".join(parts)
