"""Static HTML visualization for factory projects and pipeline runs."""

from __future__ import annotations

from collections import Counter
from html import escape
from pathlib import Path
from typing import Any

from factory.runs import queries


PIPELINE_STAGES = (
    ("spec-agent", "Story definition"),
    ("gate-1", "Spec gate"),
    ("architect-agent", "Technical approach"),
    ("gate-2", "Architecture gate"),
    ("coder-agent", "Implementation"),
)


def _status_class(status: str) -> str:
    normalized = status.lower()
    if normalized == "completed":
        return "ok"
    if normalized in {"failed", "blocked"}:
        return "bad"
    if normalized in {"waiting_human", "running"}:
        return "warn"
    return "neutral"


def _summary_cards(projects: list[dict[str, Any]], runs: list[dict[str, Any]]) -> str:
    status_counts = Counter(run["status"] for run in runs)
    cards = [
        ("Projects", str(len(projects)), "Registered project workspaces"),
        ("Runs", str(len(runs)), "Pipeline executions"),
        ("Completed", str(status_counts.get("completed", 0)), "Successful runs"),
        (
            "Needs attention",
            str(status_counts.get("failed", 0) + status_counts.get("blocked", 0)),
            "Failed or blocked runs",
        ),
    ]
    return "\n".join(
        f"""
        <article class="metric">
          <div class="metric__label">{escape(label)}</div>
          <div class="metric__value">{escape(value)}</div>
          <div class="metric__hint">{escape(hint)}</div>
        </article>
        """
        for label, value, hint in cards
    )


def _pipeline_flow() -> str:
    stages = []
    for name, hint in PIPELINE_STAGES:
        stages.append(
            f"""
            <div class="stage">
              <strong>{escape(name)}</strong>
              <span>{escape(hint)}</span>
            </div>
            """
        )
    return '<section class="flow">' + '<span class="arrow">-></span>'.join(stages) + "</section>"


def _project_section(projects: list[dict[str, Any]]) -> str:
    if not projects:
        return '<p class="empty">No projects registered yet.</p>'

    items = []
    for project in projects:
        items.append(
            f"""
            <article class="project">
              <div>
                <h3>{escape(project["name"])}</h3>
                <p>{escape(project["id"])} · {escape(project["slug"])}</p>
              </div>
              <span class="chip {_status_class(project["status"])}">{escape(project["status"].upper())}</span>
              <p class="mono">{escape(project["repo_path"])}</p>
              <p>{int(project["run_count"])} pipeline run(s)</p>
            </article>
            """
        )
    return "\n".join(items)


def _run_section(
    runs: list[dict[str, Any]],
    agent_logs: dict[int, list[dict[str, Any]]],
    gate_results: dict[int, list[dict[str, Any]]],
) -> str:
    if not runs:
        return '<p class="empty">No pipeline runs yet.</p>'

    cards = []
    for run in runs:
        run_id = int(run["id"])
        logs_html = "\n".join(
            f"""
            <li>
              <span>{escape(log["agent"])}</span>
              <strong class="{_status_class(log.get("verdict") or "")}">{escape((log.get("verdict") or "-").upper())}</strong>
            </li>
            """
            for log in agent_logs.get(run_id, [])
        ) or '<li><span>No agent logs</span><strong>-</strong></li>'
        gates_html = "\n".join(
            f"""
            <li>
              <span>{escape(gate["gate_name"])}</span>
              <strong class="{'ok' if gate["passed"] else 'bad'}">{'PASS' if gate["passed"] else 'FAIL'}</strong>
              <small>{escape(gate.get("reason") or "")}</small>
            </li>
            """
            for gate in gate_results.get(run_id, [])
        ) or '<li><span>No gate results</span><strong>-</strong></li>'
        error_html = ""
        if run.get("error"):
            error_html = f'<p class="error">{escape(run["error"])}</p>'

        cards.append(
            f"""
            <article class="run">
              <header>
                <div>
                  <h3>Run #{run_id} · {escape(run["story_id"])}</h3>
                  <p>{escape(run["project_name"])} {escape("(" + run["project_slug"] + ")" if run["project_slug"] else "")}</p>
                </div>
                <span class="chip {_status_class(run["status"])}">{escape(run["status"].upper())}</span>
              </header>
              <p class="request">{escape(run["request"])}</p>
              {error_html}
              <div class="run__details">
                <div>
                  <h4>Agents</h4>
                  <ul>{logs_html}</ul>
                </div>
                <div>
                  <h4>Gates</h4>
                  <ul>{gates_html}</ul>
                </div>
              </div>
              <p class="mono">factory review {run_id}</p>
            </article>
            """
        )
    return "\n".join(cards)


def _render_html(
    projects: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    agent_logs: dict[int, list[dict[str, Any]]],
    gate_results: dict[int, list[dict[str, Any]]],
) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Factory Flow Visualization</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #17202a;
      --muted: #657083;
      --line: #d7dde7;
      --bg: #f5f7fb;
      --panel: #ffffff;
      --ok: #147d4f;
      --warn: #9a6500;
      --bad: #b42318;
      --accent: #265dff;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--ink); background: var(--bg); }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 32px 20px 48px; }}
    h1, h2, h3, h4, p {{ margin-top: 0; }}
    h1 {{ font-size: 32px; margin-bottom: 8px; }}
    h2 {{ font-size: 20px; margin: 32px 0 12px; }}
    h3 {{ font-size: 17px; margin-bottom: 4px; }}
    h4 {{ margin-bottom: 8px; }}
    .lede {{ color: var(--muted); max-width: 760px; }}
    .metrics {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 24px 0; }}
    .metric, .project, .run, .stage {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }}
    .metric__label, .metric__hint, .project p, .run p, small {{ color: var(--muted); }}
    .metric__value {{ font-size: 28px; font-weight: 700; margin: 4px 0; }}
    .flow {{ display: grid; grid-template-columns: repeat(9, auto); gap: 8px; align-items: center; overflow-x: auto; padding-bottom: 4px; }}
    .stage {{ min-width: 150px; }}
    .stage span {{ display: block; color: var(--muted); font-size: 13px; }}
    .arrow {{ color: var(--muted); }}
    .projects {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }}
    .project {{ display: grid; gap: 8px; }}
    .run {{ margin-bottom: 14px; }}
    .run header, .project > div:first-child {{ display: flex; justify-content: space-between; gap: 16px; align-items: start; }}
    .chip {{ display: inline-flex; align-items: center; width: max-content; border-radius: 999px; padding: 4px 9px; font-size: 12px; font-weight: 700; border: 1px solid currentColor; }}
    .ok {{ color: var(--ok); }}
    .warn {{ color: var(--warn); }}
    .bad {{ color: var(--bad); }}
    .neutral {{ color: var(--muted); }}
    .request {{ color: var(--ink); background: #f8fafc; border-left: 3px solid var(--accent); padding: 10px 12px; }}
    .error {{ color: var(--bad); background: #fff5f5; border: 1px solid #ffd5d2; padding: 10px 12px; border-radius: 6px; }}
    .run__details {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
    ul {{ list-style: none; padding: 0; margin: 0; }}
    li {{ display: grid; grid-template-columns: 1fr auto; gap: 8px; padding: 7px 0; border-top: 1px solid #edf1f6; }}
    li small {{ grid-column: 1 / -1; }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 13px; word-break: break-all; }}
    .empty {{ color: var(--muted); background: var(--panel); border: 1px dashed var(--line); border-radius: 8px; padding: 16px; }}
    @media (max-width: 760px) {{
      main {{ padding: 22px 14px 36px; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .run__details {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <h1>Factory Flow Visualization</h1>
    <p class="lede">A static snapshot of projects, stories, pipeline runs, agent verdicts, gates, and review commands.</p>

    <section class="metrics">{_summary_cards(projects, runs)}</section>

    <h2>Pipeline Flow</h2>
    {_pipeline_flow()}

    <h2>Projects</h2>
    <section class="projects">{_project_section(projects)}</section>

    <h2>Runs</h2>
    <section>{_run_section(runs, agent_logs, gate_results)}</section>
  </main>
</body>
</html>
"""


def generate_factory_visualization(db_path: Path, output_path: Path) -> Path:
    """Generate a self-contained static HTML visualization of factory state."""

    report = queries.factory_report(db_path=db_path)
    projects, runs = report.projects, report.runs
    agent_logs, gate_results = report.logs_by_run, report.gates_by_run

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        _render_html(projects, runs, agent_logs, gate_results),
        encoding="utf-8",
    )
    return output_path
