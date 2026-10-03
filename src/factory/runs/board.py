"""The board's read model: one project, as the operator's design shows it.

Assembled from the durable records (workflow tables, backlog, runs, gates, agent logs);
the rules (story states, inbox order, the next start, the batch pick) are the pure
`domain.board` functions. The interface only arranges what this returns.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from factory.agent_config import tiers
from factory.agent_config.settings import settings
from factory.domain.board import NextStart, StoryFacts, inbox_group, next_start, stage_strip, story_state
from factory.domain.budget import story_spend
from factory.domain.workflow import StoryPlan
from factory.evidence.brief import load_brief
from factory.evidence.progress import run_pipeline_progress
from factory.runs.refinement import RETRYABLE_JOBS, agreement_complete
from factory.runs.status import project_status
from factory.runs.worker import worker_status
from factory.state import workflow as store
from factory.state.backlog import list_backlog
from factory.state.db import (
    get_agent_log, get_db, get_pending_human_gate, get_run, get_run_gates, init_db,
    last_agent_activity,
    project_runs, usage_rows,
)
from factory.state.interviews import list_answers
from factory.workspace.projects import get_project, list_projects

# A parked run's checkpoint, from the stage it waits at: (number, what is approved).
_CHECKPOINTS = {"gate-1-spec": (1, "spec"), "gate-2-architect": (2, "design"),
                "gate-release": (3, "release"), "gate-release-human": (3, "release")}

# What a running stage is doing, in the operator's words.
_DOING = {"spec-agent": "Writing the spec", "gate-1-spec": "Checking the spec",
          "architect-agent": "Writing the design", "boundary-review": "Reviewing the design",
          "gate-2-architect": "Checking the design", "coder-agent": "Writing code",
          "gate-build": "Building and checking the code", "tester-agent": "Testing",
          "gate-test": "Checking the tests", "release-agent": "Writing the release notes"}


@dataclass
class Decision:
    id: str  # unique on the board: "q:<session>", "d:<decision>", "run:<id>", "job:<id>"
    kind: str  # questions | backlog | brief | ckpt | release | fail
    created_at: str
    title: str
    sub: str
    short: str
    count: int = 1  # individual decisions it holds (questions left)
    story: int | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Story:
    n: int
    title: str
    state: str
    meta: str
    request: str
    stage: tuple[str, str, str, str] = ("todo", "todo", "todo", "todo")
    spend: float | None = None  # None: nothing measured yet (never a fake $0)
    files: list[str] = field(default_factory=list)
    doing: str = ""
    last_progress: str = ""
    plan: dict[str, Any] | None = None
    run_id: int | None = None
    assumptions: list[str] = field(default_factory=list)


@dataclass
class Board:
    projects: list[dict[str, Any]]
    project: dict[str, Any] | None
    lifecycle: list[tuple[str, str]] = field(default_factory=list)  # (phase, done|now|todo)
    worker: str = "idle"  # running | idle | paused | stuck
    queued: int = 0
    paused: bool = False
    brief: bool = False
    agreement: bool = False
    intake_open: bool = False
    backlog_open: bool = False
    decisions: list[Decision] = field(default_factory=list)
    stories: list[Story] = field(default_factory=list)
    jobs: list[dict[str, Any]] = field(default_factory=list)  # queued/running, in words
    activity: list[tuple[str, str]] = field(default_factory=list)  # (stamp, text), newest first
    ready_plans: list[StoryPlan] = field(default_factory=list)
    next: NextStart | None = None
    budget_usd: float = 10.0
    limit: int = 2

    @property
    def need_count(self) -> int:
        return sum(d.count for d in self.decisions)


def _parse(text: str) -> dict:
    try:
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start:end + 1]) if start != -1 else {}
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def board(project_ref: str | None, *, db_path: Path) -> Board:
    """The board for one project (the first when `project_ref` is None)."""
    init_db(db_path)
    projects = list_projects(db_path)
    if not projects:
        return Board(projects=[], project=None)
    project = get_project(db_path, project_ref) if project_ref else projects[0]
    pid, repo = project["id"], Path(project["repo_path"])
    prices = tiers.config().prices
    model = Board(projects=projects, project=project,
                  budget_usd=settings().budget.max_story_cost_usd)
    model.lifecycle = [(p.name, p.state) for p in project_status(project["slug"], db_path=db_path).phases]
    model.brief = bool(load_brief(repo))
    with get_db(db_path) as conn:
        sessions = store.list_sessions(conn, pid)
        decisions = store.list_decisions(conn, pid)
        jobs = store.list_jobs(conn, pid)
        plans = store.list_plans(conn, pid)
        events = store.list_events(conn, pid)
        rows = list_backlog(conn, pid)
        runs = project_runs(conn, pid)
        model.paused = store.is_paused(conn, pid)
        model.agreement = agreement_complete(list_answers(conn, pid))

        open_jobs = [j for j in jobs if j["status"] in ("queued", "running")]
        intake = next((s for s in sessions if s["backlog_id"] is None), None)
        model.intake_open = bool(intake and (intake["status"] in ("refining", "needs_input") or any(
            j["payload"].get("session_id") == intake["id"] for j in open_jobs)))
        model.backlog_open = any(j["kind"] == "backlog" for j in open_jobs) or any(
            d["kind"] == "backlog" and d["status"] == "pending" for d in decisions)
        model.queued = len(open_jobs)
        status = worker_status(db_path=db_path)
        model.worker = ("paused" if model.paused else "stuck" if status != "active" and open_jobs
                        else "running" if status == "active" and open_jobs else "idle")

        by_session = {s["id"]: s for s in sessions}
        titles = {r["id"]: r["title"] for r in rows}
        latest_plan = {}
        for plan in plans:
            latest_plan[plan.backlog_id] = plan
        run_by_id = {r["id"]: r for r in runs}

        # ── stories ──────────────────────────────────────────────────
        for row in rows:
            session = next((s for s in reversed(sessions) if s["backlog_id"] == row["id"]), None)
            plan = latest_plan.get(row["id"])
            run = run_by_id.get(row["run_id"]) if row["run_id"] else None
            pending = [d for d in decisions if session and d["session_id"] == session["id"]
                       and d["status"] == "pending"]
            build = next((j for j in open_jobs if plan and j["payload"].get("plan_id") == plan.id), None)
            refine = [j for j in jobs if session and j["payload"].get("session_id") == session["id"]]
            facts = StoryFacts(
                session=session["status"] if session else "", questions=len(pending),
                uncertainties=len(plan.uncertainties) if plan else 0,
                plan_ready=bool(plan and plan.ready), build_queued=bool(build),
                run=run["status"] if run else "", run_stage=(run or {}).get("current_stage") or "",
                stop_requested=bool(build and build["payload"].get("stop_requested")),
                refine_failed=bool(refine and refine[-1]["status"] in ("failed", "interrupted")))
            state, meta = story_state(facts)
            story = Story(n=row["id"], title=row["title"], state=state, meta=meta,
                          request=row["request"], plan=plan.model_dump() if plan else None,
                          files=[r.name for r in plan.resources if r.kind == "file"] if plan else [])
            if session:
                story.assumptions = [f"{a['question']} → factory’s pick: {a['answer']}"
                                     for a in session["draft"].get("answers", []) if a.get("assumed")]
            if state == "refining":
                story.doing = "Refining with your answers" if session and session["draft"].get(
                    "answers") else "Refining"
            if run:
                story.run_id = run["id"]
                progress = run_pipeline_progress(db_path, run["id"])
                story.stage = stage_strip((s.label, s.status) for s in progress)
                spent = story_spend(usage_rows(conn, run_id=run["id"]), prices).estimated_usd
                story.spend = round(spent, 2) if spent else None
                stage = run.get("current_stage") or ""
                if run["status"] == "waiting_human" and stage in _CHECKPOINTS:
                    cp, name = _CHECKPOINTS[stage]
                    story.doing = f"Waiting for you · checkpoint {cp} of 3 ({name})"
                elif run["status"] == "running":
                    story.doing = _DOING.get(stage, stage.replace("-", " ").capitalize())
                story.last_progress = last_agent_activity(conn, run["id"]) or run.get("started_at") or ""
            elif build:
                story.doing = "Starting · waiting for a free worker"
            model.stories.append(story)
        stories = {s.n: s for s in model.stories}

        # ── decisions ────────────────────────────────────────────────
        pending = [d for d in decisions if d["status"] == "pending"]
        groups: dict[str, list[dict]] = {}
        for d in pending:
            if d["kind"] == "question" and d["session_id"]:
                groups.setdefault(d["session_id"], []).append(d)
            elif d["kind"] == "backlog":
                ctx = d["context"]
                n = len(ctx.get("stories", []))
                model.decisions.append(Decision(
                    f"d:{d['id']}", "backlog", d["created_at"], "Backlog update" if rows else "Backlog",
                    f"{n} new stories", f"Backlog update · {n} new stories" if rows else
                    f"Backlog · {n} stories", data={"decision": d, "size": len(rows)}))
            elif d["kind"] == "brief":
                session = by_session.get(d["session_id"]) or {}
                change = (session.get("draft") or {}).get("amendment")
                title = "Brief change" if change else "Brief"
                model.decisions.append(Decision(
                    f"d:{d['id']}", "brief", d["created_at"], title, "ready to approve",
                    f"{title} · ready to approve", data={"decision": d, "change": change}))
            elif d["kind"] == "release":
                model.decisions.append(Decision(
                    f"d:{d['id']}", "release", d["created_at"], "Release the combined batch",
                    "verified", "Release · combined batch", data={"decision": d}))
        for session_id, group in groups.items():
            session = by_session.get(session_id) or {}
            n = session.get("backlog_id")
            subject = (f"Story #{n} {titles.get(n, '')}".rstrip() if n is not None
                       else "Interview")
            prefix = group[0]["key"].rsplit(":", 1)[0] + ":"
            answered = [d for d in decisions if d["session_id"] == session_id
                        and d["status"] == "answered" and d["key"].startswith(prefix)]
            qi, total = len(answered), len(answered) + len(group)
            left = len(group)
            sub = f"{left} question{'' if left == 1 else 's'}" + (f" · {qi} answered" if qi else "")
            model.decisions.append(Decision(
                f"q:{session_id}", "questions", min(g["created_at"] for g in group), subject, sub,
                f"{subject} · {left} question{'' if left == 1 else 's'}", count=left, story=n,
                data={"session": session, "pending": group, "answered": answered,
                      "qi": qi, "total": total}))
        for job in jobs:
            if job["status"] not in ("failed", "interrupted"):
                continue
            session = by_session.get(job["payload"].get("session_id")) or {}
            n = session.get("backlog_id")
            what = ("Backlog proposal" if job["kind"] == "backlog" else
                    f"Story #{n} {titles.get(n, '')}".rstrip() if n is not None else
                    "Interview" if session else job["kind"].capitalize())
            step = {"backlog": "Backlog proposal failed", "refine": "Refinement failed"}.get(
                job["kind"], "Work stopped" if job["status"] == "interrupted" else "Failed")
            model.decisions.append(Decision(
                f"job:{job['id']}", "fail", job["updated_at"], what, step, f"{what} · {step.lower()}",
                story=n, data={"job": job, "retryable": job["status"] == "failed"
                               and job["kind"] in RETRYABLE_JOBS}))
        for run in runs:
            if run["status"] != "waiting_human":
                continue
            gate = get_pending_human_gate(conn, run["id"])
            cp, name = _CHECKPOINTS.get(run.get("current_stage") or "", (0, "checkpoint"))
            n = next((r["id"] for r in rows if r["run_id"] == run["id"]), None)
            title = f"Story #{n} {run['story_title']}" if n is not None else f"Run #{run['id']} {run['story_title']}"
            design = _parse((get_agent_log(conn, run["id"], "architect-agent") or {}).get("output_text") or "")
            notes = _parse((get_agent_log(conn, run["id"], "release-agent") or {}).get("output_text") or "")
            spec = _parse((get_agent_log(conn, run["id"], "spec-agent") or {}).get("output_text") or "")
            claimed = {f: s.n for s in model.stories if s.n != n and s.state not in ("done",)
                       for f in s.files}
            gates = {g["gate_name"]: bool(g["passed"]) for g in get_run_gates(conn, run["id"])}
            checks = [(label, gates[g]) for g, label in (("gate-test", "tests"), ("gate-build", "build"))
                      if g in gates]
            parts = [("Changes" if (repo / m).exists() else "New", m, claimed.get(m))
                     for m in design.get("modules_affected") or []]
            data = {"run": run, "gate": gate, "cp": cp, "stage_name": name, "design": design,
                    "parts": parts, "checks": checks,
                    "release": notes, "spec": spec,
                    "questions": [q.strip() for q in ((gate or {}).get("human_questions") or "").split("\n\n") if q.strip()],
                    "story_obj": stories.get(n)}
            since = (gate or {}).get("checked_at") or run["started_at"]
            if cp == 3:
                model.decisions.append(Decision(
                    f"run:{run['id']}", "release", since, f"Release {title}",
                    "verified · ready to release", f"Release {title}", story=n, data=data))
            else:
                model.decisions.append(Decision(
                    f"run:{run['id']}", "ckpt", since,
                    title, f"Checkpoint {cp} of 3 · {name}", f"{title} · checkpoint {cp}",
                    story=n, data=data))
        model.decisions.sort(key=lambda d: (inbox_group(d.kind), d.created_at))

        # ── what is running that is not a story's build ──────────────
        for job in open_jobs:
            session = by_session.get(job["payload"].get("session_id")) or {}
            if job["kind"] == "refine" and session.get("backlog_id") is not None:
                continue  # shown as its story, refining
            if job["kind"] == "build":
                continue
            text = {"backlog": "Revising the backlog proposal" if job["payload"].get("feedback")
                    else "Proposing a backlog from the brief",
                    "refine": "Writing the brief" if session.get("phase") == "publish"
                    else "Preparing the interview questions"}.get(job["kind"], job["kind"].capitalize())
            model.jobs.append({"text": text, "at": job["created_at"], "job": job})

        model.activity = [(e["created_at"], text) for e in reversed(events)
                          if (text := _event_text(e, decisions))]

    model.ready_plans = [latest_plan[s.n] for s in model.stories
                         if s.state == "ready" and s.n in latest_plan]
    question = next((d for d in model.decisions if d.kind == "questions"), None)
    model.next = next_start(brief=model.brief, intake_open=model.intake_open, paused=model.paused,
                            ready=model.ready_plans, stories=len(model.stories),
                            backlog_open=model.backlog_open,
                            question_subject=question.title if question else "",
                            project=project["slug"], limit=model.limit)
    return model


_EVENT_TEXT = {
    "backlog: needs_input": "Backlog proposal ready for your review",
    "refine: needs_input": "Refinement has questions for you",
    # The plan event ("Story #n: plan ready") says these once already.
    "refine: ready": "",
    "refine: blocked": "",
    "refine: refining": "Refinement moved to its next step",
}


def _answer_text(decision: dict) -> str:
    """What was chosen, in words: an option number is the option's label."""
    raw = (decision.get("answer") or "").strip()
    options = (decision["context"].get("question") or {}).get("options") or []
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1]["label"]
    return "left to the factory" if raw.lower() == "you decide" else raw


def _event_text(event: dict, decisions: list[dict]) -> str:
    details = event.get("details") or {}
    message = event["message"]
    if message == "Decision answered":
        decision = next((d for d in decisions if d["id"] == details.get("decision_id")), None)
        if decision:
            answer = _answer_text(decision)
            return f"Answered: {decision['question']}" + (f" → {answer}" if answer else "")
    if event["kind"] in ("failed", "interrupted"):
        kind, _, what = message.partition(": ")
        step = {"refine": "Refinement", "backlog": "Backlog proposal"}.get(kind, kind.capitalize())
        what = re.sub(r"\s*\(exit -?\d+\)", "", what).split(". ", 1)[0]
        return f"✗ {step} failed: {what}"
    if (prepared := re.fullmatch(r"Story (\d+) prepared", message)):
        return f"Story #{prepared.group(1)}: plan ready"
    return _EVENT_TEXT.get(message, message)  # "" drops a duplicate


def now() -> datetime:
    return datetime.now(timezone.utc)


def run_of(run_id: int, *, db_path: Path) -> dict | None:
    with get_db(db_path) as conn:
        return get_run(conn, run_id)
