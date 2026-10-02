"""docs/work/BACKLOG.md: the approved story list, in delivery order, with status.

Rendered from the `backlog_stories` rows; the DB is the record, this is its
version-controlled copy in the product repo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

BACKLOG_RELPATH = "docs/work/BACKLOG.md"


def render_backlog(project_name: str, rows: list[dict[str, Any]]) -> str:
    parts = [f"# Backlog — {project_name}"]
    for r in rows:
        status = r["status"] + (f" ({r['story_id']})" if r.get("story_id") else "")
        lines = [f"{r['position']}. **{r['title']}** — {status}", f"   {r['request']}"]
        if r.get("rationale"):
            lines.append(f"   Why: {r['rationale']}")
        parts.append("\n".join(lines))
    if not rows:
        parts.append("(empty)")
    return "\n\n".join(parts) + "\n"


def write_backlog(project_dir: Path, project_name: str, rows: list[dict[str, Any]]) -> Path:
    """Write BACKLOG.md. Does not commit — the caller owns the commit."""
    path = Path(project_dir) / BACKLOG_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_backlog(project_name, rows), encoding="utf-8")
    return path
