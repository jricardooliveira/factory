"""`factory status`: one project's lifecycle, its spend and the next command."""

from __future__ import annotations

from rich.text import Text

from factory.interfaces.render import output
from factory.runs import ProjectStatus

_MARK = {"done": "✅", "now": "▶ ", "todo": "· "}
_STYLE = {"done": "green", "now": "bold yellow", "todo": "dim"}


def status_lines(status: ProjectStatus) -> list[str]:
    """Plain text, shared by the CLI and the board."""
    f = status.facts
    lines = [f"{status.name} ({status.project_id}, {f.slug}) — {status.repo_path}"]
    lines += [f"  {_MARK[p.state]} {p.name:<8} {p.detail}" for p in status.phases]
    for story in f.backlog:
        state = (story.run.status.replace("_", " ") + f" (run #{story.run.id})"
                 if story.run else {"approved": "to do"}.get(story.status, story.status))
        lines.append(f"       {story.position:>2}. {story.title} — {state}")
    lines.append(f"  Spend: intake ${f.intake_usd:.2f} · stories ${f.stories_usd:.2f}")
    lines.append(f"Next: {status.next.command}   ({status.next.why})")
    if status.next.alternative:
        lines.append(f"  or: {status.next.alternative}")
    return lines


def print_status(statuses: list[ProjectStatus]) -> None:
    if not statuses:
        output.console.print("No projects yet. Start with: factory project create <slug>")
        return
    for status in statuses:
        output.console.print()
        for line in status_lines(status):
            style = next((_STYLE[p.state] for p in status.phases
                          if line.lstrip().startswith(_MARK[p.state] + " " + p.name)), "")
            if line.startswith("Next:"):
                style = "bold cyan"
            output.console.print(Text(line, style=style))
